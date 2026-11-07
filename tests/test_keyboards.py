from app.keyboards import persistent_menu


def test_reply_menu_does_not_force_persistent_mobile_keyboard() -> None:
    menu = persistent_menu()

    assert menu.is_persistent is False
