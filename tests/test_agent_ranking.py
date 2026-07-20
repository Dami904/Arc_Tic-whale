from server.api import _rank_agent_cards


def _card(agent_id, win_rate):
    return {"id": agent_id, "win_rate": win_rate}


class TestRankAgentCards:
    def test_sorts_by_win_rate_descending(self):
        cards = [_card("A", 20.0), _card("B", 80.0), _card("C", 50.0)]
        ranked = _rank_agent_cards(cards)
        assert [c["id"] for c in ranked] == ["B", "C", "A"]

    def test_assigns_one_indexed_rank(self):
        cards = [_card("A", 20.0), _card("B", 80.0)]
        ranked = _rank_agent_cards(cards)
        assert ranked[0]["rank"] == 1
        assert ranked[1]["rank"] == 2

    def test_ties_preserve_original_order(self):
        cards = [_card("A", 50.0), _card("B", 50.0), _card("C", 50.0)]
        ranked = _rank_agent_cards(cards)
        assert [c["id"] for c in ranked] == ["A", "B", "C"]

    def test_zero_win_rate_agents_rank_last_not_excluded(self):
        cards = [_card("A", 0.0), _card("B", 10.0)]
        ranked = _rank_agent_cards(cards)
        assert len(ranked) == 2
        assert ranked[-1]["id"] == "A"
