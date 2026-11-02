import json

import httpx2
import pytest
from agent_core.testing import USAGE, chunk, make_client, sse_response, tool_call_chunks
from erp_store.db import ProductRow, StockRow, init_db, make_engine
from erpilot_api.main import create_app
from langgraph.checkpoint.memory import InMemorySaver
from mcp_erp.session_tools import SessionErpTools
from sqlalchemy.orm import Session


@pytest.mark.parametrize('ambiguous', [False, True])
async def test_natural_order_sse_clarifies_or_saves_and_restores_exact_requirements(
    tmp_path, ambiguous,
):
    path = tmp_path / 'natural-api.db'
    engine = make_engine(path)
    init_db(engine)
    with Session(engine) as session:
        for sku, name, price in [('M1', '无线鼠标', 100), ('K1', '机械键盘', 200)]:
            session.add(ProductRow(sku=sku, name=name, price=price, category='测试', status='在售'))
            session.add(StockRow(sku=sku, quantity=1000, warehouse='主仓'))
        if ambiguous:
            session.add(ProductRow(sku='M2', name='无线鼠标', price=150, category='测试',
                                   status='在售'))
        session.commit()
    provider = await SessionErpTools.create(path)
    calls = []

    def model(request):
        messages = json.loads(request.content)['messages']
        calls.append(messages)
        last = json.loads(messages[-1]['content'])
        if last.get('status') == 'clarification_required':
            return sse_response([
                chunk(delta={'content': '无线鼠标有 M1、M2 两个候选，请明确 SKU。'}),
                chunk(usage=USAGE),
            ])
        if last.get('status') == 'resolved':
            args = {'customer': last['customer'], 'items': last['items'],
                    'note': '分箱发货', 'budget_limit': 1000}
            return sse_response(tool_call_chunks('create', 'prepare_order_draft', json.dumps(args)))
        return sse_response([
            chunk(delta={'content': '草稿已保存，请核对全部商品；未建单、未扣库存。'}),
            chunk(usage=USAGE),
        ])

    saver = InMemorySaver()
    options = dict(client_factory=lambda: make_client(model), session_erp_tools=provider,
                   checkpointer=saver, trace_dir=tmp_path / 'traces')
    app = create_app(**options)
    source = ('给客户甲准备订单：两件无线鼠标，机械键盘三件；'
              '备注原文「分箱发货」；预算上限为1000元；只保存草稿')
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app), base_url='http://test',
    ) as client:
        response = await client.post('/api/chat/stream',
                                     json={'session_id': 's', 'message': source})
        assert response.status_code == 200 and 'event: done' in response.text
        assert 'event: approval_pending' not in response.text
        assert 'resolve_order_items' in response.text
        task = (await client.get('/api/sessions/s/order-task')).json()
        assert (await client.get('/api/sessions/other/order-task')).json()['status'] == 'none'
    try:
        assert len(calls) == (1 if ambiguous else 2)
        # The first model request already contains an application-selected catalogue read.
        assert json.loads(calls[0][-1]['content'])['source_user_sha256']
        if ambiguous:
            assert task['status'] == 'none' and provider.drafts.list_latest('s') == []
        else:
            draft = task['draft']
            assert [(r['sku'], r['quantity']) for r in draft['items']] == [('M1', 2), ('K1', 3)]
            assert draft['total_amount'] == 800 and draft['budget_limit'] == 1000
            assert draft['note'] == '分箱发货'
            assert draft['item_constraint']['origin']['kind'] == 'catalog_resolved'
            restored = await SessionErpTools.create(path)
            recovered = create_app(**{**options, 'session_erp_tools': restored})
            async with httpx2.AsyncClient(transport=httpx2.ASGITransport(recovered),
                                         base_url='http://test') as client:
                current = (await client.get('/api/sessions/s/order-task')).json()['draft']
                assert current['item_constraint'] == draft['item_constraint']
                assert current['draft_id'] == draft['draft_id'] and current['revision'] == 1
            assert len(calls) == 2  # Restoring the quote does not ask the model again.
    finally:
        engine.dispose()
