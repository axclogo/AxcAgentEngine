from axc_agent_engine.utils.json_utils import extract_json_array, extract_json_object


def test_extract_json_object_handles_empty_invalid_fence_and_balanced_strings():
	assert extract_json_object("") == {}
	assert extract_json_object("not json") == {}
	assert extract_json_object("prefix ```json\n{\"a\":1}\n``` suffix") == {"a": 1}
	assert extract_json_object('noise {"text":"brace } inside","escaped":"quote \\" ok"} tail')["text"] == "brace } inside"
	assert extract_json_object('noise {"a": 1') == {}


def test_extract_json_array_handles_fence_nested_invalid_and_non_array():
	assert extract_json_array("") is None
	assert extract_json_array("{\"a\":1}") is None
	assert extract_json_array("prefix ```json\n[{\"a\":1}, {\"b\":[2]}]\n``` suffix") == [{"a": 1}, {"b": [2]}]
	assert extract_json_array('noise [{"text":"bracket ] inside"}] tail') == [{"text": "bracket ] inside"}]
	assert extract_json_array("prefix [1, 2") is None
