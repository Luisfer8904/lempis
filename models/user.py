"""
Usuarios y roles del SaaS.
Cada User pertenece a un Tenant y puede tener uno o más Roles dentro de él.
"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import relationship
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin

from models import db, login_manager
from models.base import TimestampMixin, tenant_fk


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


class Role(db.Model, TimestampMixin):
    """
    Roles globales del sistema.
    Por defecto: owner, admin, vendedor, contador, viewer.
    """
    __tablename__ = "lempis_roles"

    id = Column(Integer, primary_key=True)
    code = Column(String(40), unique=True, nullable=False)   # owner, admin, ...
    name = Column(String(80), nullable=False)
    description = Column(String(255))

    user_roles = relationship("UserRole", back_populates="role", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Role {self.code}>"


class User(UserMixin, db.Model, TimestampMixin):
    """
    Usuario del SaaS. Pertenece a un Tenant (empresa).
    """
    __tablename__ = "lempis_usuarios"
    __table_args__ = (
        UniqueConstraint("tenant_id", "email", name="uq_lempis_usuarios_tenant_email"),
    )

    id = Column(Integer, primary_key=True)
    tenant_id = tenant_fk()

    # Identidad
    email = Column(String(160), nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    full_name = Column(String(120))
    phone = Column(String(40))
    avatar_url = Column(String(255))
    branch_id = Column(Integer, ForeignKey("lempis_sedes.id", ondelete="SET NULL"), nullable=True)

    # Estado
    is_active = Column(Boolean, default=True, nullable=False)
    is_owner = Column(Boolean, default=False)  # creador del tenant
    is_superadmin = Column(Boolean, default=False, nullable=False)  # admin de la plataforma Lempis
    email_verified = Column(Boolean, default=False)
    last_login_at = Column(DateTime)

    # Reset password
    reset_token = Column(String(120), index=True)
    reset_token_expires = Column(DateTime)

    # Relaciones
    tenant = relationship("Tenant", back_populates="users")
    branch = relationship("Branch")
    user_roles = relationship("UserRole", back_populates="user", cascade="all, delete-orphan")

    # ----- Helpers de password -----
    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    # ----- Helpers de roles -----
    @property
    def roles(self):
        return [ur.role for ur in self.user_roles]

    def has_role(self, code: str) -> bool:
        return any(r.code == code for r in self.roles)

    def has_permission(self, permission: str) -> bool:
        if self.is_owner or self.has_role("owner"):
            return True
        from services.permissions import user_has_permission
        return user_has_permission(self, permission)

    def is_admin(self) -> bool:
        return self.is_owner or self.has_role("admin") or self.has_role("owner")

    # ----- Login tracking -----
    def touch_login(self) -> None:
        self.last_login_at = datetime.utcnow()

    def __repr__(self):
        return f"<User {self.email} tenant={self.tenant_id}>"


class UserRole(db.Model, TimestampMixin):
    """Relación N:M entre usuarios y roles, scoped por tenant."""
    __tablename__ = "lempis_usuarios_roles"
    __table_args__ = (
        UniqueConstraint("user_id", "role_id", name="uq_lempis_usuarios_roles_user_role"),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("lempis_usuarios.id", ondelete="CASCADE"), nullable=False, index=True)
    role_id = Column(Integer, ForeignKey("lempis_roles.id", ondelete="CASCADE"), nullable=False, index=True)
    tenant_id = tenant_fk()

    user = relationship("User", back_populates="user_roles")
    role = relationship("Role", back_populates="user_roles")


class RolePermission(db.Model, TimestampMixin):
    """Permisos configurables por tenant y rol."""
    __tablename__ = "lempis_roles_permisos"
    __table_args__ = (
        UniqueConstraint("tenant_id", "role_id", name="uq_lempis_roles_permisos_tenant_role"),
    )

    id = Column(Integer, primary_key=True)
    tenant_id = tenant_fk()
    role_id = Column(Integer, ForeignKey("lempis_roles.id", ondelete="CASCADE"), nullable=False, index=True)
    permissions = Column(Text, nullable=False, default="[]")

    role = relationship("Role")
