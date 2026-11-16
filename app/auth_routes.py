from __future__ import annotations

import re
from functools import lru_cache

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_user, logout_user

from .auth import (
    PROFILE_FIELDS,
    authenticate_local,
    admin_required,
    find_user_by_name,
    get_user,
    list_users,
    oidc_authorize_callback,
    oidc_authorize_redirect,
    oidc_enabled,
    set_display_name,
    update_user_contact,
    update_user_profile,
    update_user_role,
)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_RE = re.compile(r"^[0-9+().\-/\s#xXext]*$")
_MAX_LEN = {"display_name": 120, "alt_contact_name": 120, "chat_handle": 100,
            "contact_email": 254, "institution": 200, "timezone": 64}
_PHONE_FIELDS = ("phone", "mobile_phone", "alt_contact_phone")


@lru_cache(maxsize=1)
def timezone_names() -> tuple:
    try:
        from zoneinfo import available_timezones
        return tuple(sorted(available_timezones()))
    except Exception:  # pragma: no cover - zoneinfo/tzdata unavailable
        return ()


def _valid_timezone(name: str) -> bool:
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(name)
        return True
    except Exception:
        return False


def _validate_profile(form: dict) -> list[str]:
    errors = []
    for field, value in form.items():
        limit = 64 if field in _PHONE_FIELDS else _MAX_LEN.get(field, 200)
        if len(value) > limit:
            errors.append(f"{field.replace('_', ' ').capitalize()} is longer than {limit} characters.")
    for field in _PHONE_FIELDS:
        if form.get(field) and not _PHONE_RE.match(form[field]):
            errors.append(f"{field.replace('_', ' ').capitalize()} may only contain digits, spaces, + ( ) - . / # and ext.")
    if form.get("contact_email") and not _EMAIL_RE.match(form["contact_email"]):
        errors.append("Contact email is not a valid email address.")
    if form.get("timezone") and not _valid_timezone(form["timezone"]):
        errors.append(f"Unknown timezone '{form['timezone']}'. Use an IANA name such as America/Chicago.")
    return errors


def _rename_self(user, new_name: str) -> str | None:
    """Change the user's display name and rename their stored records.

    Returns an error message, or None on success. Blank reverts to the SSO
    name. Refuses names held by another account, or by a contact whose email
    is not one of this user's addresses.
    """
    from app import preferences, store

    custom = True
    if not new_name:
        if not user.sso_name:
            return "Display name cannot be blank."
        new_name, custom = user.sso_name, False
    elif user.sso_name and new_name == user.sso_name:
        custom = False
    if new_name == user.name:
        if custom != user.name_custom:
            set_display_name(user.get_id(), new_name, custom)
        return None

    other = find_user_by_name(new_name, exclude_id=user.get_id())
    if other:
        return f"The name '{new_name}' is already used by another account."
    if new_name.lower() != user.name.lower() and store.contact_name_conflict(
        new_name, {user.email, user.contact_email}
    ):
        return (f"The name '{new_name}' belongs to an existing contact with a different email. "
                "Ask an administrator to merge the records.")

    counts = store.rename_person(user.name, new_name)
    prefs = preferences.rename_submissions(user.name, new_name)
    set_display_name(user.get_id(), new_name, custom)
    current_app.logger.info(
        "user %s renamed %r -> %r (assignments=%d contacts=%d preferences=%d)",
        user.get_id(), user.name, new_name, counts["assignments"], counts["contacts"], prefs,
    )
    return None


def _sync_contact(user) -> None:
    """Mirror a user's account contact fields into the calendar contacts table."""
    from app import store

    store.upsert_contact(
        user.name,
        email=user.contact_address,
        phone=user.phone,
        institution=user.institution,
        replace=True,
        extra={k: getattr(user, k, "") for k in store.CONTACT_EXTRA_FIELDS},
    )

bp = Blueprint("auth", __name__)


@bp.route("/login")
def login():
    return render_template(
        "login.html",
        oidc_enabled=oidc_enabled(),
        show_admin_login=current_app.config.get("SHOW_ADMIN_LOGIN", False),
    )


@bp.route("/login/local", methods=["POST"])
def local_login():
    user = authenticate_local(
        request.form.get("email", ""),
        request.form.get("password", ""),
    )
    if not user or not user.is_admin:
        flash("Invalid administrator credentials.", "danger")
        return redirect(url_for("auth.login"))

    login_user(user)
    return redirect(request.args.get("next") or url_for("main.calendar_view"))


@bp.route("/login/oidc")
def oidc_login():
    if not oidc_enabled():
        flash("Fermilab SSO is not configured.", "warning")
        return redirect(url_for("auth.login"))
    return oidc_authorize_redirect()


@bp.route("/oidc/callback")
def oidc_callback():
    try:
        user = oidc_authorize_callback()
    except Exception as exc:
        flash(f"Fermilab SSO login failed: {exc}", "danger")
        return redirect(url_for("auth.login"))
    login_user(user)
    from flask import session
    return redirect(session.pop("oidc_next", None) or url_for("main.calendar_view"))


@bp.route("/logout", methods=["POST", "GET"])
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))


@bp.route("/profile", methods=["GET", "POST"])
def profile():
    if not current_user.is_authenticated:
        return redirect(url_for("auth.login", next="/profile"))
    if request.method == "POST":
        fields = ("display_name", "phone", "institution") + PROFILE_FIELDS
        form = {k: request.form.get(k, "").strip() for k in fields if k in request.form}
        errors = _validate_profile(form)
        user = get_user(current_user.get_id())
        if not errors and "display_name" in form:
            error = _rename_self(user, form["display_name"])
            if error:
                errors.append(error)
        if errors:
            for message in errors:
                flash(message, "danger")
            return render_template("profile.html", nav_active="profile", form=form,
                                   timezones=timezone_names()), 400
        form.pop("display_name", None)
        updated = update_user_profile(current_user.get_id(), form)
        if updated:
            _sync_contact(updated)
            flash("Profile updated.", "success")
        else:
            flash("Could not update your profile.", "danger")
        return redirect(url_for("auth.profile"))
    return render_template("profile.html", nav_active="profile", form=None,
                           timezones=timezone_names())


@bp.route("/admin/users")
@admin_required
def users():
    return render_template("admin_users.html", users=list_users())


@bp.route("/admin/users/<int:user_id>/contact", methods=["POST"])
@admin_required
def update_contact(user_id: int):
    updated = update_user_contact(
        str(user_id),
        request.form.get("phone", ""),
        request.form.get("institution", ""),
    )
    if updated:
        _sync_contact(updated)
        flash(f"Updated contact info for {updated.email}.", "success")
    else:
        flash("User not found.", "danger")
    return redirect(url_for("auth.users"))


@bp.route("/admin/users/<int:user_id>/role", methods=["POST"])
@admin_required
def update_role(user_id: int):
    ok, message = update_user_role(
        str(user_id),
        request.form.get("role", ""),
        current_user.get_id(),
    )
    flash(message, "success" if ok else "danger")
    if str(user_id) == current_user.get_id() and request.form.get("role") != "admin":
        logout_user()
        return redirect(url_for("auth.login"))
    return redirect(url_for("auth.users"))
