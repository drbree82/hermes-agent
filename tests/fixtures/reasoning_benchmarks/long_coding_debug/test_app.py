from app import process_batches


def test_processes_each_batch_from_the_start():
    assert process_batches([["a", "b"], ["c"], ["d", "e"]]) == [
        "a", "b", "c", "d", "e"
    ]


def test_empty_batches_do_not_change_the_cursor():
    assert process_batches([["a"], [], ["b"]]) == ["a", "b"]
