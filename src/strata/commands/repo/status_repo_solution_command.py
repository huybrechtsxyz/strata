"""Command to show git state for all registered repositories."""

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, cast

import click

from strata.commands.base_command import BaseCommand
from strata.integrations.factory import IntegrationFactory
from strata.integrations.git import GitIntegration
from strata.models.integration_model import IntegrationModel
from strata.models.repository_model import RemoteModel
from strata.services.configuration_service import ConfigurationService


class StatusRepoSolutionCommand(BaseCommand):
    """Show git working-tree state for all (or one) registered repositories.

    For each repo that is present on disk it reports:

    - Current branch and tracking remote
    - Ahead / behind counts vs. the remote
    - Staged, unstaged, untracked, and conflicted file counts
    - A clean / dirty summary flag

    Repos that are not yet cloned show as ``not cloned``.

    Pass ``--name`` to inspect a single repository.
    Pass ``--verbose`` to list individual changed files.
    """

    OPERATION = "solution_repo_status"

    def __init__(
        self,
        name: Optional[str] = None,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: Optional[bool] = None,
        quiet: Optional[bool] = None,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._filter_name = name
        self._configuration_service: Optional[ConfigurationService] = None

    def get_required_integrations(self) -> Dict[str, str]:
        return {"git": "repository status"}

    # -------------------------------------------------------------------------
    # Execution
    # -------------------------------------------------------------------------

    def _execute(self) -> bool:
        repos, errors = self._solution_controller.get_repositories(self._filter_name)
        if errors:
            self._errors.extend(errors)
            return False
        if not repos:
            self._errors.append("No repositories registered in solution.")
            return False

        git = self._get_git_integration()
        if git is None:
            return False

        # Best-effort: link solution repos to configured remotes by normalized URL,
        # so tag classification uses conventions declared once on spec.remotes[].conventions
        # (never guessed from the tag name itself). Non-fatal when unavailable.
        remotes_by_url = self._build_remotes_by_url()

        results: List[Dict[str, Any]] = []
        for repo in repos:
            local_path = Path(repo.path)
            if not local_path.is_absolute():
                local_path = self._work_path / local_path

            entry: Dict[str, Any] = {
                "name": str(repo.name),
                "path": str(local_path),
                "url": repo.url,
                "branch_registered": repo.branch,
            }

            if not local_path.exists() or not (local_path / ".git").exists():
                entry["state"] = "not_cloned"
            else:
                ok, status = git.status(str(local_path))
                if not ok:
                    entry["state"] = "error"
                else:
                    remote_url = git.get_remote_url(str(local_path))
                    entry.update(
                        {
                            "state": "clean" if status.is_clean else "dirty",
                            "branch": status.branch,
                            "tracking": status.tracking,
                            "ahead": status.ahead,
                            "behind": status.behind,
                            "staged": status.staged,
                            "unstaged": status.unstaged,
                            "untracked": status.untracked,
                            "conflicted": status.conflicted,
                            "remote_url": remote_url,
                        }
                    )

                    # Discover tags — classified using the linked remote's declared
                    # conventions (matched by normalized remote URL). No link, no
                    # classification: honest over guessed.
                    matched_remote = remotes_by_url.get(self._normalize_repo_url(remote_url)) if remote_url else None
                    tags = self._discover_tags(git, str(local_path), matched_remote)
                    if tags:
                        entry["tags"] = tags

            results.append(entry)

        self._output_data = {"repos": results}
        return True

    def _after_execute(self) -> bool:
        if not self._is_quiet() and self._is_console_output():
            self._print_results(self._output_data.get("repos", []))
        return True

    # -------------------------------------------------------------------------
    # Console output
    # -------------------------------------------------------------------------

    def _print_results(self, results: List[Dict[str, Any]]) -> None:
        click.echo("")
        for r in results:
            state = r.get("state", "?")

            if state == "not_cloned":
                click.echo(f"  ⚪  {r['name']}  (not cloned — run `strata repo sync`)")
                continue

            if state == "error":
                click.echo(f"  ❓  {r['name']}  (could not read git status)")
                continue

            icon = "✅" if state == "clean" else "⚠️ "
            branch = r.get("branch") or r.get("branch_registered") or "?"
            tracking = r.get("tracking")
            ahead = r.get("ahead", 0)
            behind = r.get("behind", 0)

            # Build the branch/tracking string
            track_str = ""
            if tracking:
                parts = []
                if ahead:
                    parts.append(f"↑{ahead}")
                if behind:
                    parts.append(f"↓{behind}")
                track_str = f"  →  {tracking}"
                if parts:
                    track_str += f"  [{', '.join(parts)}]"

            staged = len(r.get("staged", []))
            unstaged = len(r.get("unstaged", []))
            untracked = len(r.get("untracked", []))
            conflicted = len(r.get("conflicted", []))

            click.echo(f"  {icon}  {r['name']}  [{branch}{track_str}]")

            if state == "dirty":
                parts = []
                if staged:
                    parts.append(f"{staged} staged")
                if unstaged:
                    parts.append(f"{unstaged} unstaged")
                if untracked:
                    parts.append(f"{untracked} untracked")
                if conflicted:
                    parts.append(f"{conflicted} conflicted")
                click.echo(f"       {', '.join(parts)}")

                if self._output_verbose:
                    for f in r.get("staged", []):
                        click.echo(f"         S  {f}")
                    for f in r.get("unstaged", []):
                        click.echo(f"         M  {f}")
                    for f in r.get("untracked", []):
                        click.echo(f"         ?  {f}")
                    for f in r.get("conflicted", []):
                        click.echo(f"         !  {f}")

            # Show tags if discovered
            tags = r.get("tags")
            if tags:
                latest_release = tags.get("latest_release")
                latest_quality = tags.get("latest_quality")
                if latest_release or latest_quality:
                    click.echo("       Tags:")
                    if latest_release:
                        click.echo(
                            f"         Release: {latest_release['name']} ({latest_release['age_str']}, {latest_release['commit']})"
                        )
                    if latest_quality:
                        click.echo(
                            f"         Quality: {latest_quality['name']} ({latest_quality['age_str']}, {latest_quality['commit']})"
                        )

        click.echo("")

    # -------------------------------------------------------------------------
    # Tag discovery
    # -------------------------------------------------------------------------

    def _build_remotes_by_url(self) -> Dict[str, RemoteModel]:
        """Best-effort: map normalized remote URL -> RemoteModel for remotes with conventions.

        Loading configuration is entirely optional here — ``strata repo status``
        must keep working with zero active profile/configuration. Any failure
        just means no repo gets tag classification (same as no link found).
        """
        try:
            self._configuration_service = self._load_configuration_service_best_effort()
            if self._configuration_service is None:
                return {}
            remotes = self._configuration_service.get_remotes() or []
            return {
                self._normalize_repo_url(remote.repository): remote
                for remote in remotes
                if remote.conventions is not None
            }
        except Exception as exc:
            self.logger.debug(f"Skipping remote-convention linking: {exc}")
            return {}

    def _load_configuration_service_best_effort(self) -> Optional[ConfigurationService]:
        """Load ConfigurationService from the active profile's configfile_paths, if any.

        Returns None (never raises) when there's no active profile, no
        configfile_paths, or loading fails for any reason — this is advisory
        data only, not a hard requirement for ``repo status`` to function.
        """
        from strata.utils.system import resolve_path

        if self._solution_controller.solution is None:
            return None

        profile, _ = self._solution_controller.get_active_profile()
        if profile is None:
            return None

        configfile_paths = profile.configfile_paths or []
        if not configfile_paths:
            return None

        repo_map = self._solution_controller.get_repo_map()
        resolved_paths = []
        for entry in configfile_paths:
            try:
                resolved = resolve_path(str(self._work_path), str(entry.path), repo_map=repo_map)
            except ValueError:
                continue
            if resolved.exists():
                resolved_paths.append(str(resolved))

        if not resolved_paths:
            return None

        try:
            ConfigurationService.reset()
            config_svc = ConfigurationService.get_instance()
            success, _load_errors = config_svc.load_from_paths(resolved_paths)
            if not success:
                return None
            return config_svc
        except Exception as exc:
            self.logger.debug(f"ConfigurationService load failed (non-fatal): {exc}")
            return None

    @staticmethod
    def _normalize_repo_url(url: str) -> str:
        """Normalize a git remote URL for identity comparison (not full parsing).

        Strips scheme, trailing '.git', trailing slash, and converts the
        'git@host:org/repo' SCP-like form to 'host/org/repo', then lowercases.
        This lets 'git@github.com:acme/x.git' and 'https://github.com/acme/x'
        compare equal — same repository, different string forms.
        """
        u = url.strip().rstrip("/")
        if u.endswith(".git"):
            u = u[:-4]
        u = re.sub(r"^([\w.-]+)@([^:]+):", r"\2/", u)  # git@host:org/repo -> host/org/repo
        u = re.sub(r"^\w+://", "", u)  # strip scheme (https://, ssh://, git://)
        return u.lower()

    def _discover_tags(
        self, git: GitIntegration, repo_path: str, matched_remote: Optional[RemoteModel]
    ) -> Optional[Dict[str, Any]]:
        """Discover latest release and quality-gate tags for a linked remote.

        Args:
            git: GitIntegration instance
            repo_path: Path to the repository
            matched_remote: The configuration remote whose declared URL matches
                this repo's actual git remote URL, or None if unlinked.

        Returns:
            Dict with latest_release and latest_quality tag info, or None when
            unlinked (no matching remote) or no tags match its conventions.
        """
        if matched_remote is None or matched_remote.conventions is None:
            # No verified link to a configured remote — don't guess.
            return None

        conventions = matched_remote.conventions
        if not conventions.release_pattern and not conventions.quality_pattern:
            return None

        try:
            all_tags = git.list_tags(repo_path, timeout=30)
            if not all_tags:
                return None

            latest_release = self._find_latest_matching(all_tags, conventions.release_pattern)
            latest_quality = self._find_latest_matching(all_tags, conventions.quality_pattern)

            if not latest_release and not latest_quality:
                return None

            result: Dict[str, Any] = {}

            if latest_release:
                result["latest_release"] = {
                    "name": latest_release.name,
                    "commit": latest_release.short_commit,
                    "created": latest_release.created.isoformat() if latest_release.created else None,
                    "age_days": latest_release.age_days,
                    "age_str": latest_release.age_str,
                }

            if latest_quality:
                result["latest_quality"] = {
                    "name": latest_quality.name,
                    "commit": latest_quality.short_commit,
                    "created": latest_quality.created.isoformat() if latest_quality.created else None,
                    "age_days": latest_quality.age_days,
                    "age_str": latest_quality.age_str,
                }

            return result if result else None

        except Exception as exc:
            # Silently skip tag discovery on error
            self.logger.debug(f"Tag discovery failed for {repo_path}: {exc}")
            return None

    @staticmethod
    def _find_latest_matching(tags: List[Any], pattern: Optional[str]) -> Optional[Any]:
        """Return the first (newest, since list_tags sorts newest-first) tag matching pattern."""
        if not pattern:
            return None
        try:
            compiled = re.compile(pattern)
        except re.error:
            return None
        for tag in tags:
            if compiled.fullmatch(tag.name):
                return tag
        return None

    # -------------------------------------------------------------------------
    # Git integration factory (mirrors RepositoryController pattern)
    # -------------------------------------------------------------------------

    def _get_git_integration(self) -> Optional[GitIntegration]:
        try:
            config = IntegrationModel(
                name="git",
                type="git",
                description="Git integration for repository status",
                validation=None,
                authentication=None,
                endpoints=None,
                lifecycle=None,
            )
            git_classes = IntegrationFactory.get_registered_types()
            git_class = git_classes.get("git")
            if not git_class:
                self._errors.append("Git integration is not registered")
                return None

            git = cast(GitIntegration, git_class(config=config))
            available, error = git.ensure_available()
            if not available:
                self._errors.append(f"Git is not available: {error}")
                return None

            return git

        except Exception as exc:
            self._errors.append(f"Failed to initialise Git integration: {exc}")
            self.logger.error("Failed to initialise Git integration", exc_info=True)
            return None
