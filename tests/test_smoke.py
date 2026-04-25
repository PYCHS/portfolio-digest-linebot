import src


def test_package_importable():
    assert src.__version__ == "0.1.0"
