from database import change_balance

def admin_credit(user_id, amount, note="Admin credit"):
    if amount <= 0:
        return False, 0
    return change_balance(user_id, amount, "credit", note)

def admin_debit(user_id, amount, note="Admin debit"):
    if amount <= 0:
        return False, 0
    return change_balance(user_id, -amount, "debit", note)
