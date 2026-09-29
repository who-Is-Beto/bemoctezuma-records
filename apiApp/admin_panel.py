"""What a custom role (Django Group) can grant inside the "Administración" page.

Three levels, each only meaningful when the previous one is granted:
  1. ACCESS            — the user can open Administración at all.
  2. a tab permission  — the user sees that tab/section (and can read its data).
  3. tab actions       — what the user may do inside it.

Single source of truth for the Roles tab UI (``/roles/catalog/``), role
validation (``normalize_role_codenames``) and the permission names the admin
views check via ``_require_admin``. Creating roles, assigning roles and
changing a user's ADMIN/CUSTOMER role stay ADMIN-only and are not listed here.
"""

ACCESS = 'access_admin_panel'
ACCESS_LABEL = 'Acceder a Administración'

# (tab id — matches the frontend, label, tab permission, [(action permission, label)])
TABS = [
    ('add-record', 'Agregar disco', 'tab_add_record', [
        ('add_record', 'Crear discos'),
    ]),
    ('manage-records', 'Punto de venta', 'tab_manage_records', [
        ('change_record', 'Editar y vender discos'),
        ('delete_record', 'Eliminar discos'),
    ]),
    ('manage-bazares', 'Manejo de bazares', 'tab_manage_bazares', [
        ('add_bazar', 'Crear bazares'),
        ('change_bazar', 'Editar bazares'),
        ('delete_bazar', 'Eliminar bazares'),
    ]),
    ('manage-orders', 'Pedidos', 'tab_manage_orders', [
        ('change_order', 'Actualizar estado y guía de envío'),
    ]),
    # Read-only history; selling itself is change_record in Punto de venta.
    ('sales', 'Ventas', 'tab_sales', []),
    ('manage-users', 'Gestionar usuarios', 'tab_manage_users', [
        ('delete_user', 'Eliminar usuarios (no administradores)'),
    ]),
    # Not a tab: the maintenance card at the top of Administración. Reuses
    # Django's SiteConfig permissions. change_siteconfig also lets the user past
    # the maintenance gate (middleware), so they can close the window.
    ('maintenance', 'Ventana de mantenimiento', 'view_siteconfig', [
        ('change_siteconfig', 'Abrir y cerrar la ventana'),
    ]),
]

# Custom permissions declared on User.Meta: ACCESS + the ``tab_*`` ones
# (everything else is a Django default permission on its own model).
CUSTOM_PERMISSIONS = [(ACCESS, ACCESS_LABEL)] + [
    (tab_perm, f'Ver pestaña {label}') for _, label, tab_perm, _ in TABS if tab_perm.startswith('tab_')
]

CODENAMES = {ACCESS} | {
    code for _, _, tab_perm, actions in TABS for code in [tab_perm, *(a for a, _ in actions)]
}


def normalize_role_codenames(codes):
    """Drop anything whose parent level isn't granted: tabs without ACCESS,
    actions without their tab. Keeps stored roles consistent with the UI."""
    codes = set(codes)
    if ACCESS not in codes:
        return set()
    kept = {ACCESS}
    for _, _, tab_perm, actions in TABS:
        if tab_perm in codes:
            kept |= {tab_perm} | {a for a, _ in actions if a in codes}
    return kept
