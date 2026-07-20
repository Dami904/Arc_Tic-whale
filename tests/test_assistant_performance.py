from unittest.mock import patch

from backend.assistant import _primary_follow_agent_id, _user_pnl_windows


class TestPrimaryFollowAgentId:
    def test_returns_largest_allocation(self):
        allocations = [
            {"agent_id": "A", "allocation": 20.0},
            {"agent_id": "B", "allocation": 80.0},
        ]
        assert _primary_follow_agent_id(allocations) == "B"

    def test_returns_none_for_no_follows(self):
        assert _primary_follow_agent_id([]) is None


class TestUserPnlWindows:
    def test_not_following_returns_na(self):
        result = _user_pnl_windows("user1", [])
        assert result["24h"] == "N/A (not following an agent yet)"

    @patch("backend.assistant.get_follower_performance")
    def test_uses_real_follower_performance_for_primary_follow(self, mock_perf):
        mock_perf.return_value = {"24h": "+5.00%", "7d": "+8.00%", "1y": None}
        allocations = [{"agent_id": "Conservative_Whale", "allocation": 50.0}]

        result = _user_pnl_windows("user1", allocations)

        mock_perf.assert_called_once_with("user1", "Conservative_Whale")
        assert result["24h"] == "+5.00%"
        assert result["7d"] == "+8.00%"
        assert result["1y"] == "insufficient data yet"  # None -> honest fallback text, not "0.00%"
