"""Lifecycle of a backpressured model stream. 验证流式背压时的任务生命周期。"""

import asyncio

from axc_agent_engine import Event
from axc_agent_engine.core.execution_run import StreamLLMBridge


async def test_closing_full_stream_releases_model_producer(exec_ctx):
	producer_blocked = asyncio.Event()
	producer_closed = asyncio.Event()

	class Producer:
		async def call(self, ctx, messages, tools, stream_sink):
			try:
				for index in range(2000):
					if index == 1001:
						producer_blocked.set()
					await stream_sink.emit(Event.delta(str(index)))
			finally:
				producer_closed.set()

	stream = StreamLLMBridge(Producer(), exec_ctx).call([], None)
	assert (await anext(stream)).content == "0"
	await asyncio.wait_for(producer_blocked.wait(), 2)
	await asyncio.wait_for(stream.aclose(), 2)
	assert producer_closed.is_set()
