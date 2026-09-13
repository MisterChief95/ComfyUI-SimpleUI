"""Host-scoped and profile-scoped settings (AUTH-001)."""

from .service import HOST_SETTINGS, PROFILE_SETTINGS, SettingsError, SettingsStore

__all__ = ["HOST_SETTINGS", "PROFILE_SETTINGS", "SettingsError", "SettingsStore"]
