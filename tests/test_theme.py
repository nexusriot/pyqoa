import theme


def test_font_scale_rebinds_the_size_tokens():
    base = theme._FONT_BASE["FS_MD"]
    try:
        theme.set_font_scale(2.0)
        assert theme.FS_MD == round(base * theme.FONT_SCALE_MAX)
    finally:
        theme.set_font_scale(1.0)
    assert theme.FS_MD == base


def test_font_scale_is_clamped_both_ways():
    assert theme.clamp_font_scale(99) == theme.FONT_SCALE_MAX
    assert theme.clamp_font_scale(0.01) == theme.FONT_SCALE_MIN
    assert theme.clamp_font_scale("nonsense") == 1.0


def test_app_point_size_follows_the_scale():
    try:
        theme.set_font_scale(1.5)
        assert theme.app_point_size() == round(theme.BASE_POINT_SIZE * 1.5)
    finally:
        theme.set_font_scale(1.0)


def test_theme_switch_keeps_the_font_scale():
    try:
        theme.set_font_scale(1.3)
        theme.apply("light")
        assert theme.FONT_SCALE == 1.3
        assert theme.NAME == "light"
    finally:
        theme.apply("dark")
        theme.set_font_scale(1.0)


def test_palettes_define_the_same_tokens():
    assert set(theme._DARK) == set(theme._LIGHT)
