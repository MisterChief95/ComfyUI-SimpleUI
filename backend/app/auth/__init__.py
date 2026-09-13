"""Profiles, sessions, and the local trust boundary (AUTH-001)."""

from .service import AdmissionClosed, AdmissionGate, AuthError, AuthService, Principal

__all__ = ["AdmissionClosed", "AdmissionGate", "AuthError", "AuthService", "Principal"]
