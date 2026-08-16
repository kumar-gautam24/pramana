from policy.retrieval import reciprocal_rank_fusion


def test_fuses_two_rankings():
    dense = [10, 20, 30]
    lexical = [30, 10, 40]

    fused = reciprocal_rank_fusion([dense, lexical])
    order = [chunk_id for chunk_id, _ in fused]

    assert order[0] == 10
    assert set(order) == {10, 20, 30, 40}


def test_a_document_ranked_well_by_both_beats_one_ranked_well_by_either():
    """This is the whole point of fusing: agreement across two different notions of
    relevance outranks a strong showing in one."""
    fused = dict(reciprocal_rank_fusion([[1, 2], [1, 3]]))

    assert fused[1] > fused[2]
    assert fused[1] > fused[3]


def test_scores_descend():
    fused = reciprocal_rank_fusion([[5, 6, 7], [7, 6, 5]])
    scores = [score for _, score in fused]

    assert scores == sorted(scores, reverse=True)


def test_an_empty_ranking_contributes_nothing():
    assert reciprocal_rank_fusion([[1, 2], []]) == reciprocal_rank_fusion([[1, 2]])


def test_no_rankings_yields_nothing():
    assert reciprocal_rank_fusion([]) == []


def test_fused_scores_carry_no_relevance_information():
    """The top-ranked chunk always scores 1/(k+1) regardless of whether it is relevant,
    which is exactly why the gate cannot threshold on an RRF score and the cross-encoder
    has to stay. See docs/decisions/0007."""
    one = reciprocal_rank_fusion([[42]])
    other = reciprocal_rank_fusion([[99]])

    assert one[0][1] == other[0][1]
