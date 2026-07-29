from stores.models import Store, StoreMembership


def require_store_access(user, store_id, *, roles=None):
    store = Store.objects.get(id=store_id)
    if user.is_superuser:
        return store
    membership = StoreMembership.objects.filter(store=store, user=user).first()
    if membership is None:
        raise PermissionError("해당 매장에 접근할 권한이 없습니다.")
    if roles and membership.role not in roles:
        raise PermissionError("이 작업을 수행할 권한이 없습니다.")
    return store
