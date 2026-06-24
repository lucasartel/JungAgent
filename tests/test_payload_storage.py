from payload_storage import persistable_image_url, sanitize_persisted_payload


def test_persistable_image_url_accepts_only_external_urls():
    assert persistable_image_url("https://example.com/image.png") == "https://example.com/image.png"
    assert persistable_image_url("http://example.com/image.jpg") == "http://example.com/image.jpg"
    assert persistable_image_url("data:image/png;base64,AAAA") is None
    assert persistable_image_url("not-a-url") is None
    assert persistable_image_url(None) is None


def test_sanitize_persisted_payload_redacts_image_data_url():
    payload = {
        "image_url": "data:image/png;base64," + ("A" * 16000),
        "text": "texto linguistico preservado",
    }

    sanitized = sanitize_persisted_payload(payload)

    assert sanitized["image_url"]["redacted"] == "image_data_url"
    assert sanitized["image_url"]["chars"] > 16000
    assert sanitized["text"] == "texto linguistico preservado"


def test_sanitize_persisted_payload_redacts_large_encoded_blob():
    sanitized = sanitize_persisted_payload("A" * 20000)

    assert sanitized["redacted"] == "large_encoded_blob"
    assert sanitized["chars"] == 20000
