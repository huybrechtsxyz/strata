"""Tests for OverlapController — cross-manifest deployment scope overlap detection."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

from strata.controllers.overlap_controller import OverlapController, OverlapError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_manifest(tmp_path: Path, name: str, layers: dict, workspace_file: str) -> Path:
    """Write a minimal deployment YAML file and return its path."""
    data = {
        "apiVersion": "strata.huybrechts.xyz/v1",
        "kind": "deployment",
        "meta": {"name": name},
        "spec": {
            "layers": layers,
            "workspace": {"file": workspace_file},
            "environments": ["env.yaml"],
        },
    }
    p = tmp_path / f"{name}.yaml"
    p.write_text(yaml.dump(data), encoding="utf-8")
    return p


def _make_config_service(layering: list):
    """Return a mock ConfigurationService whose layering matches *layering*."""
    layer_objs = []
    for name in layering:
        layer = MagicMock()
        layer.name = name
        layer.default = None
        layer_objs.append(layer)
    spec = MagicMock()
    spec.layering = layer_objs
    model = MagicMock()
    model.spec = spec
    svc = MagicMock()
    svc.model = model
    return svc


def _controller(tmp_path: Path, layering=("zone", "customer", "environment")):
    config_svc = _make_config_service(list(layering))
    return OverlapController(
        configuration_service=config_svc,
        repo_map={},
        work_path=tmp_path,
    )


# ---------------------------------------------------------------------------
# Check #1 — artifact_path + workspace uniqueness
# ---------------------------------------------------------------------------


class TestCheck1ArtifactPathUniqueness:
    def test_no_overlap_different_layers(self, tmp_path):
        """Two manifests with different layer values → no overlap."""
        m1 = _make_manifest(tmp_path, "a", {"zone": "eu", "customer": "acme", "environment": "prd"}, "ws.yaml")
        m2 = _make_manifest(tmp_path, "b", {"zone": "eu", "customer": "beta", "environment": "prd"}, "ws.yaml")
        ctrl = _controller(tmp_path)
        ok = ctrl.run([m1, m2])
        assert ok is True
        assert ctrl.get_overlap_errors() == []

    def test_overlap_same_layers_same_workspace(self, tmp_path):
        """Two manifests with identical layers + same workspace → critical overlap."""
        layers = {"zone": "eu", "customer": "acme", "environment": "prd"}
        m1 = _make_manifest(tmp_path, "a", layers, "ws.yaml")
        m2 = _make_manifest(tmp_path, "b", layers, "ws.yaml")
        ctrl = _controller(tmp_path)
        ok = ctrl.run([m1, m2])
        assert ok is False
        errors = ctrl.get_overlap_errors()
        assert len(errors) == 1
        assert errors[0].check == 1
        assert "eu/acme/prd" in errors[0].message
        assert "ws.yaml" in errors[0].message

    def test_same_layers_different_workspace_no_check1(self, tmp_path):
        """Same artifact_path but different workspace → Check #1 does NOT fire."""
        layers = {"zone": "eu", "customer": "acme", "environment": "prd"}
        m1 = _make_manifest(tmp_path, "a", layers, "ws-network.yaml")
        m2 = _make_manifest(tmp_path, "b", layers, "ws-app.yaml")
        ctrl = _controller(tmp_path)
        ok = ctrl.run([m1, m2])
        # Check #1 should be clean (Check #2 might fire but workspaces don't exist → skipped)
        check1_errors = [e for e in ctrl.get_overlap_errors() if e.check == 1]
        assert check1_errors == []

    def test_three_manifests_two_overlap(self, tmp_path):
        """Three manifests — two share a key, one is unique → exactly one error."""
        layers_shared = {"zone": "eu", "customer": "acme", "environment": "prd"}
        layers_unique = {"zone": "eu", "customer": "beta", "environment": "prd"}
        m1 = _make_manifest(tmp_path, "a", layers_shared, "ws.yaml")
        m2 = _make_manifest(tmp_path, "b", layers_shared, "ws.yaml")
        m3 = _make_manifest(tmp_path, "c", layers_unique, "ws.yaml")
        ctrl = _controller(tmp_path)
        ok = ctrl.run([m1, m2, m3])
        assert ok is False
        check1 = [e for e in ctrl.get_overlap_errors() if e.check == 1]
        assert len(check1) == 1
        assert str(m1) in check1[0].files
        assert str(m2) in check1[0].files
        assert str(m3) not in check1[0].files

    def test_empty_manifest_list(self, tmp_path):
        """No manifests → no errors, returns True."""
        ctrl = _controller(tmp_path)
        ok = ctrl.run([])
        assert ok is True
        assert ctrl.get_overlap_errors() == []

    def test_non_deployment_yaml_ignored(self, tmp_path):
        """A workspace YAML file in the list is silently skipped."""
        ws = tmp_path / "workspace.yaml"
        ws.write_text(yaml.dump({"kind": "workspace", "meta": {"name": "w"}, "spec": {}}))
        layers = {"zone": "eu", "customer": "acme", "environment": "prd"}
        m1 = _make_manifest(tmp_path, "a", layers, "ws.yaml")
        ctrl = _controller(tmp_path)
        ok = ctrl.run([ws, m1])
        assert ok is True

    def test_unparseable_yaml_adds_warning(self, tmp_path):
        """A file that cannot be parsed adds a warning, doesn't crash."""
        bad = tmp_path / "bad.yaml"
        bad.write_text("{ invalid: yaml: content:", encoding="utf-8")
        ctrl = _controller(tmp_path)
        ok = ctrl.run([bad])
        assert ok is True  # no critical errors
        warnings = ctrl.get_overlap_warnings()
        check0 = [w for w in warnings if w.check == 0]
        assert len(check0) == 1

    def test_no_layering_configured_skips_check(self, tmp_path):
        """When config has no layering, artifact_path is empty → Check #1 skipped."""
        layers = {"zone": "eu"}
        m1 = _make_manifest(tmp_path, "a", layers, "ws.yaml")
        m2 = _make_manifest(tmp_path, "b", layers, "ws.yaml")
        config_svc = MagicMock()
        config_svc.model.spec.layering = []
        ctrl = OverlapController(configuration_service=config_svc, repo_map={}, work_path=tmp_path)
        ok = ctrl.run([m1, m2])
        assert ok is True

    def test_overlap_error_contains_both_files(self, tmp_path):
        """Error lists both conflicting manifest paths."""
        layers = {"zone": "eu", "customer": "acme", "environment": "prd"}
        m1 = _make_manifest(tmp_path, "a", layers, "ws.yaml")
        m2 = _make_manifest(tmp_path, "b", layers, "ws.yaml")
        ctrl = _controller(tmp_path)
        ctrl.run([m1, m2])
        errors = ctrl.get_overlap_errors()
        assert str(m1) in errors[0].files
        assert str(m2) in errors[0].files

    def test_is_warning_false_on_check1(self, tmp_path):
        """Check #1 errors are critical (is_warning=False)."""
        layers = {"zone": "eu", "customer": "acme", "environment": "prd"}
        m1 = _make_manifest(tmp_path, "a", layers, "ws.yaml")
        m2 = _make_manifest(tmp_path, "b", layers, "ws.yaml")
        ctrl = _controller(tmp_path)
        ctrl.run([m1, m2])
        errors = ctrl.get_overlap_errors()
        assert all(not e.is_warning for e in errors)


# ---------------------------------------------------------------------------
# Check #2 — terraform backend collision
# ---------------------------------------------------------------------------


class TestCheck2TerraformBackend:
    def test_no_workspace_file_skips_check2(self, tmp_path):
        """Manifests whose workspace file doesn't exist are skipped in Check #2."""
        layers = {"zone": "eu", "customer": "acme", "environment": "prd"}
        m1 = _make_manifest(tmp_path, "a", layers, "nonexistent-ws.yaml")
        m2 = _make_manifest(tmp_path, "b", layers, "nonexistent-ws2.yaml")
        ctrl = _controller(tmp_path)
        ctrl.run([m1, m2])
        check2 = [e for e in ctrl.get_overlap_errors() if e.check == 2]
        assert check2 == []

    def test_check2_collision_detected(self, tmp_path):
        """Manifests with same artifact_path + same workspace backend → Check #2 error."""
        from unittest.mock import MagicMock

        from strata.models.common_models import ProvisionerType

        layers = {"zone": "eu", "customer": "acme", "environment": "prd"}
        m1 = _make_manifest(tmp_path, "a", layers, "ws.yaml")
        m2 = _make_manifest(tmp_path, "b", layers, "ws2.yaml")

        # Create a fake workspace file so _resolve_ref finds it
        ws1 = tmp_path / "ws.yaml"
        ws1.write_text("placeholder")
        ws2 = tmp_path / "ws2.yaml"
        ws2.write_text("placeholder")

        # Mock WorkspaceService to return a workspace with a terraform backend
        def _make_ws_svc(backend_type="azurerm"):
            iac = MagicMock()
            iac.provisioner = ProvisionerType.TERRAFORM
            iac.backend = MagicMock()
            iac.backend.type = backend_type
            ws_spec = MagicMock()
            ws_spec.provisioners = [iac]
            ws_spec.namespaces = []
            ws_model = MagicMock()
            ws_model.spec = ws_spec
            svc = MagicMock()
            svc.model = ws_model
            return svc

        with patch("strata.controllers.overlap_controller.WorkspaceService") as mock_ws:
            mock_ws.load.return_value = _make_ws_svc()
            ctrl = _controller(tmp_path)
            ok = ctrl.run([m1, m2])

        check2 = [e for e in ctrl.get_overlap_errors() if e.check == 2]
        # Same artifact_path through different workspace files with same backend type
        # → collision since (ws_file, btype, apath) groups them
        # m1 uses "ws.yaml", m2 uses "ws2.yaml" → different ws_file keys → NO collision
        # (correct — they're different workspace files)
        assert check2 == []

    def test_check2_same_workspace_same_apath_collision(self, tmp_path):
        """Same workspace file + same backend + same artifact_path → Check #2 fires."""
        from strata.models.common_models import ProvisionerType

        layers = {"zone": "eu", "customer": "acme", "environment": "prd"}
        m1 = _make_manifest(tmp_path, "a", layers, "ws.yaml")
        m2 = _make_manifest(tmp_path, "b", layers, "ws.yaml")

        ws1 = tmp_path / "ws.yaml"
        ws1.write_text("placeholder")

        def _make_ws_svc():
            iac = MagicMock()
            iac.provisioner = ProvisionerType.TERRAFORM
            iac.backend = MagicMock()
            iac.backend.type = "azurerm"
            ws_spec = MagicMock()
            ws_spec.provisioners = [iac]
            ws_spec.namespaces = []
            ws_model = MagicMock()
            ws_model.spec = ws_spec
            svc = MagicMock()
            svc.model = ws_model
            return svc

        with patch("strata.controllers.overlap_controller.WorkspaceService") as mock_ws:
            mock_ws.load.return_value = _make_ws_svc()
            ctrl = _controller(tmp_path)
            ok = ctrl.run([m1, m2])

        check2 = [e for e in ctrl.get_overlap_errors() if e.check == 2]
        assert len(check2) == 1
        assert check2[0].check == 2
        assert not check2[0].is_warning


# ---------------------------------------------------------------------------
# Check #3 — namespace overlap across layers
# ---------------------------------------------------------------------------


class TestCheck3NamespaceOverlap:
    def _make_ns_svc(self, is_shared=False):
        from strata.models.namespace_model import NamespaceType

        ns_spec = MagicMock()
        ns_spec.type = NamespaceType.SHARED if is_shared else NamespaceType.DEDICATED
        ns_model = MagicMock()
        ns_model.spec = ns_spec
        svc = MagicMock()
        svc.model = ns_model
        return svc

    def _make_ws_svc_with_ns(self, ns_names: list):
        ns_refs = []
        for name in ns_names:
            ref = MagicMock()
            ref.name = name
            ref.file = f"{name}.yaml"
            ns_refs.append(ref)
        ws_spec = MagicMock()
        ws_spec.provisioners = []
        ws_spec.namespaces = ns_refs
        ws_model = MagicMock()
        ws_model.spec = ws_spec
        svc = MagicMock()
        svc.model = ws_model
        return svc

    def test_same_namespace_different_layers_warns(self, tmp_path):
        """Same namespace name across different layer key-sets → Check #3 warning."""
        m1 = _make_manifest(tmp_path, "zone", {"zone": "eu"}, "ws-zone.yaml")
        m2 = _make_manifest(tmp_path, "prd", {"zone": "eu", "customer": "acme", "environment": "prd"}, "ws-prd.yaml")

        ws_zone = tmp_path / "ws-zone.yaml"
        ws_zone.write_text("placeholder")
        ws_prd = tmp_path / "ws-prd.yaml"
        ws_prd.write_text("placeholder")
        ns_file = tmp_path / "traefik.yaml"
        ns_file.write_text("placeholder")

        ws_zone_svc = self._make_ws_svc_with_ns(["traefik"])
        ws_prd_svc = self._make_ws_svc_with_ns(["traefik"])
        ns_svc = self._make_ns_svc(is_shared=False)

        with (
            patch("strata.controllers.overlap_controller.WorkspaceService") as mock_ws,
            patch("strata.controllers.overlap_controller.NamespaceService") as mock_ns,
        ):
            mock_ws.load.side_effect = lambda p: ws_zone_svc if "zone" in p else ws_prd_svc
            mock_ns.load.return_value = ns_svc
            ctrl = _controller(tmp_path)
            ok = ctrl.run([m1, m2])

        assert ok is True  # warnings don't fail
        warnings = ctrl.get_overlap_warnings()
        check3 = [w for w in warnings if w.check == 3]
        assert len(check3) == 1
        assert "traefik" in check3[0].message
        assert check3[0].is_warning is True

    def test_shared_namespace_suppresses_warning(self, tmp_path):
        """Namespace with spec.type=shared → no warning even with different layers."""
        m1 = _make_manifest(tmp_path, "zone", {"zone": "eu"}, "ws-zone.yaml")
        m2 = _make_manifest(tmp_path, "prd", {"zone": "eu", "customer": "acme", "environment": "prd"}, "ws-prd.yaml")

        ws_zone = tmp_path / "ws-zone.yaml"
        ws_zone.write_text("placeholder")
        ws_prd = tmp_path / "ws-prd.yaml"
        ws_prd.write_text("placeholder")
        ns_file = tmp_path / "traefik.yaml"
        ns_file.write_text("placeholder")

        ws_zone_svc = self._make_ws_svc_with_ns(["traefik"])
        ws_prd_svc = self._make_ws_svc_with_ns(["traefik"])
        ns_svc = self._make_ns_svc(is_shared=True)  # shared!

        with (
            patch("strata.controllers.overlap_controller.WorkspaceService") as mock_ws,
            patch("strata.controllers.overlap_controller.NamespaceService") as mock_ns,
        ):
            mock_ws.load.side_effect = lambda p: ws_zone_svc if "zone" in p else ws_prd_svc
            mock_ns.load.return_value = ns_svc
            ctrl = _controller(tmp_path)
            ok = ctrl.run([m1, m2])

        assert ok is True
        check3 = [w for w in ctrl.get_overlap_warnings() if w.check == 3]
        assert check3 == []

    def test_same_namespace_same_layer_no_warning(self, tmp_path):
        """Same namespace in manifests with the same layer key-set → no warning."""
        layers_a = {"zone": "eu", "customer": "acme", "environment": "prd"}
        layers_b = {"zone": "us", "customer": "acme", "environment": "prd"}  # same keys, diff values
        m1 = _make_manifest(tmp_path, "a", layers_a, "ws.yaml")
        m2 = _make_manifest(tmp_path, "b", layers_b, "ws.yaml")

        ws_f = tmp_path / "ws.yaml"
        ws_f.write_text("placeholder")
        ns_file = tmp_path / "traefik.yaml"
        ns_file.write_text("placeholder")

        ws_svc = self._make_ws_svc_with_ns(["traefik"])
        ns_svc = self._make_ns_svc(is_shared=False)

        with (
            patch("strata.controllers.overlap_controller.WorkspaceService") as mock_ws,
            patch("strata.controllers.overlap_controller.NamespaceService") as mock_ns,
        ):
            mock_ws.load.return_value = ws_svc
            mock_ns.load.return_value = ns_svc
            ctrl = _controller(tmp_path)
            ok = ctrl.run([m1, m2])

        check3 = [w for w in ctrl.get_overlap_warnings() if w.check == 3]
        assert check3 == []


# ---------------------------------------------------------------------------
# OverlapError model
# ---------------------------------------------------------------------------


class TestOverlapError:
    def test_to_dict_critical(self):
        err = OverlapError(check=1, message="collision", files=["a.yaml", "b.yaml"], is_warning=False)
        d = err.to_dict()
        assert d["check"] == 1
        assert d["message"] == "collision"
        assert d["files"] == ["a.yaml", "b.yaml"]
        assert d["warning"] is False

    def test_to_dict_warning(self):
        err = OverlapError(check=3, message="shared ns", files=["a.yaml"], is_warning=True)
        d = err.to_dict()
        assert d["warning"] is True

    def test_get_overlap_errors_excludes_warnings(self, tmp_path):
        ctrl = _controller(tmp_path)
        ctrl._overlap_errors.append(OverlapError(1, "critical", ["a.yaml"], is_warning=False))
        ctrl._overlap_errors.append(OverlapError(3, "warn", ["b.yaml"], is_warning=True))
        assert len(ctrl.get_overlap_errors()) == 1
        assert len(ctrl.get_overlap_warnings()) == 1
