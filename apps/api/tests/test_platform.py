from app.services.platform_detection import detect_platform


def test_shopify_from_html_and_headers():
    html = '<script src="https://cdn.shopify.com/s/files/1/theme.js"></script><div class="shopify-section">'
    result = detect_platform(html=html, headers={"x-shopify-stage": "production"}, url="https://store.example")
    assert result.platform_name == "Shopify"
    assert result.confidence >= 0.9


def test_wordpress_woocommerce():
    html = '<link href="/wp-content/plugins/woocommerce/assets/css/woocommerce.css">'
    result = detect_platform(html=html)
    assert result.platform_name == "WooCommerce"
    names = {t["name"] for t in result.technologies}
    assert "WordPress" in names


def test_wix():
    html = '<script src="https://static.wixstatic.com/app.js"></script>'
    result = detect_platform(html=html)
    assert result.platform_name == "Wix"


def test_webflow():
    html = '<html data-wf-site="abc123"></html>'
    result = detect_platform(html=html)
    assert result.platform_name == "Webflow"


def test_unknown_without_signals():
    result = detect_platform(html="")
    assert result.platform_name == "Unknown"


def test_custom_when_html_but_no_platform():
    result = detect_platform(html="<html><body>Hello</body></html>", url="https://custom.example/about")
    assert result.platform_name in {"Other", "Unknown"}
