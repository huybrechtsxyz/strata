from xyz_platform.validators.base_validator import BaseValidator


class PlatformValidator(BaseValidator):
    """Validator for platform-specific configuration and state."""

    def before_validate(self, work_path: Path) -> bool:
        # No pre-validation steps needed for now
        return True

    def validate(self, work_path: Path) -> bool:
        # Placeholder for future platform-specific validation logic
        return True

    def after_validate(
        self,
        work_path: Path,
    ) -> bool:
        # No post-validation steps needed for now
        return True
