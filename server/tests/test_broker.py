import asyncio

from api.broker import Broker


def test_publish_reaches_only_the_same_team():
    async def scenario():
        broker = Broker()
        team_a = broker.subscribe("a")
        team_b = broker.subscribe("b")

        broker.publish("a", {"id": 1})

        assert await asyncio.wait_for(team_a.get(), timeout=1) == {"id": 1}
        assert team_b.empty()

    asyncio.run(scenario())


def test_unsubscribed_queue_gets_nothing():
    async def scenario():
        broker = Broker()
        queue = broker.subscribe("a")
        broker.unsubscribe("a", queue)
        broker.publish("a", {"id": 1})
        assert queue.empty()
        assert broker.subscriber_count("a") == 0

    asyncio.run(scenario())
