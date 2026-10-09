from app.services.analysis import analyze_website_signals
from app.services.crawler import detect_lead_capture


def test_detects_classic_html_form():
    html = """
    <form action="/contact">
      <input name="full_name" placeholder="Your name">
      <input type="email" name="email">
      <textarea name="message"></textarea>
    </form>
    """
    assert detect_lead_capture(html)


def test_detects_inputs_without_form_tag():
    html = """
    <div class="lead-box">
      <input id="email" placeholder="Work email">
      <input name="phone" type="tel">
      <button>Request a quote</button>
    </div>
    """
    assert detect_lead_capture(html)


def test_detects_hubspot_and_typeform_embeds():
    assert detect_lead_capture('<script>hbspt.forms.create({portalId:"123"})</script>')
    assert detect_lead_capture('<iframe src="https://form.typeform.com/to/abc"></iframe>')
    assert detect_lead_capture('<div class="wix-form-grid"></div>')


def test_does_not_flag_plain_marketing_page():
    html = "<main><h1>About our plumbing team</h1><p>We fix leaks in Miami.</p></main>"
    assert not detect_lead_capture(html, "We fix leaks in Miami.")


def test_analysis_skips_missing_form_when_capture_found():
    result = analyze_website_signals(
        text="Contact us",
        title="Home",
        meta_description="",
        signals={"reachable": True, "https": True, "has_lead_capture": True},
        pages=[],
        platform="wix",
    )
    assert "no_lead_capture" not in {i["code"] for i in result["issues"]}
