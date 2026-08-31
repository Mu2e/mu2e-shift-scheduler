from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_user, logout_user

from .auth import (
    authenticate_local,
    admin_required,
    list_users,
    oidc_authorize_callback,
    oidc_authorize_redirect,
    oidc_enabled,
    update_user_role,
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


@bp.route("/admin/users")
@admin_required
def users():
    return render_template("admin_users.html", users=list_users())


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
