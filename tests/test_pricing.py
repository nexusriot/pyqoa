import pricing


def test_longest_matching_key_wins():
    mini = pricing.estimate_cost("gpt-4o-mini", 1_000_000, 0)
    full = pricing.estimate_cost("gpt-4o", 1_000_000, 0)
    assert mini == 0.15 and full == 2.50


def test_o_series_mini_is_not_priced_as_the_base_model():
    assert pricing.estimate_cost("o1-mini", 1_000_000, 0) == 3.00
    assert pricing.estimate_cost("o1", 1_000_000, 0) == 15.00


def test_provider_prefixes_still_match():
    assert pricing.estimate_cost("openai/gpt-4o", 1_000_000, 0) == 2.50


def test_unknown_model_reports_nothing():
    assert pricing.estimate_cost("llama3", 1000, 1000) is None
    assert pricing.estimate_cost("", 1000, 1000) is None


def test_completion_tokens_use_the_output_price():
    assert pricing.estimate_cost("gpt-4o", 0, 1_000_000) == 10.00
