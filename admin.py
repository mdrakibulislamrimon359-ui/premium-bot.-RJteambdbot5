from config import ADMIN_IDS, ADMIN_USERNAME

def is_admin(user):
    return (
        user.id in ADMIN_IDS
        or (user.username and user.username.lower() == ADMIN_USERNAME.lower())
    )
