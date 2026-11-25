from shop.cart import Cart

def test_total():
    assert Cart().total() == '0.00'
