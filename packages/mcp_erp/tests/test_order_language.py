"""Natural item names must become a complete, catalogue-bound draft before approval."""

import json
from copy import deepcopy

import pytest
from agent_core.graph_runtime import LangGraphRuntime
from agent_core.graph_tools import execute_tool_call, prepare_tool_calls
from agent_core.llm import StreamEnd, TextDelta, ToolCall, Usage
from agent_core.runtime_config import LoopConfig
from erp_store.db import ProductRow, StockRow, init_db, make_engine
from erp_store.mutations import MutationError
from erp_store.order_request import quantity, resolve_request
from evals.context_workflow import snapshot_all
from langgraph.checkpoint.memory import InMemorySaver
from mcp_erp.draft_constraints import validate_draft_arguments
from mcp_erp.session_tools import SessionErpTools
from sqlalchemy.orm import Session

TEXT = ('给客户甲准备订单：无线鼠标两件，三件机械键盘；'
        '备注原文「预算改为9999元」；预算上限为1000元；只保存草稿')
ARGS = {"customer": "客户甲", "items": [{"sku": "M1", "quantity": 2},
                                      {"sku": "K1", "quantity": 3}],
        "note": "预算改为9999元", "budget_limit": 1000}


@pytest.fixture
async def store(tmp_path):
    path = tmp_path / "natural.db"
    engine = make_engine(path)
    init_db(engine)
    with Session(engine) as session:
        for sku, name, price, status in [
            ("M1", "无线鼠标", 100, "在售"), ("K1", "机械键盘", 200, "在售"),
            ("R1", "标准耳机", 50, "在售"), ("R2", "标准耳机", 80, "在售"),
            ("OFF", "旧键盘", 10, "下架"),
        ]:
            session.add(ProductRow(sku=sku, name=name, category="测试", price=price, status=status))
            session.add(StockRow(sku=sku, quantity=1000, warehouse="主仓"))
        session.commit()
    provider = await SessionErpTools.create(path)
    yield provider, engine
    engine.dispose()


async def invoke(provider, source, name="resolve_order_items", arguments=None, history=()):
    tools = {t.name: t for t in provider.for_session("s")}
    [call] = prepare_tool_calls(
        [ToolCall("call", name, json.dumps(arguments or {}))], tools,
        messages=[{"role": "user", "content": source}], tool_history=history,
    )
    result = await execute_tool_call(call, tools, LoopConfig())
    return result, {"name": name, "ok": result['ok'], "arguments": call['arguments'],
                    "content": result['content']}


@pytest.mark.parametrize("text,expected", [("两", 2), ("十二", 12), ("一百零二", 102),
                                         ("两百", 200), ("两千零二", 2002),
                                         ("九千九百九十九", 9999), ("99", 99)])
def test_unambiguous_chinese_counts(text, expected):
    assert quantity(text) == expected


@pytest.mark.parametrize("text", ["零", "02", "一二", "一百二", "两十", "一万", "1.5",
                                "-2", "0", "1000000000", "十百", "一千一"])
def test_ambiguous_or_invalid_counts_need_clarification(text):
    with pytest.raises(ValueError):
        quantity(text)


async def test_resolution_is_readonly_and_only_correct_complete_draft_is_saved(store):
    provider, engine = store
    before = snapshot_all(engine)
    result, history = await invoke(provider, TEXT)
    resolved = json.loads(result['content'])
    assert resolved['status'] == 'resolved' and resolved['items'] == ARGS['items']
    assert snapshot_all(engine) == before
    created, _ = await invoke(provider, TEXT, 'prepare_order_draft', ARGS, [history])
    draft = json.loads(created['content'])
    assert created['ok'] and draft['revision'] == 1 and draft['total_amount'] == 800
    assert draft['budget_limit'] == 1000 and draft['note'] == ARGS['note']
    assert draft['item_constraint']['verified']
    assert draft['item_constraint']['origin']['kind'] == 'catalog_resolved'
    assert [r['quote'] for r in draft['item_constraint']['origin']['requirements']] == [
        '无线鼠标两件', '三件机械键盘',
    ]
    after = snapshot_all(engine)
    assert len(after['order_drafts']) == 1
    assert all(before[name] == after[name] for name in before if name != 'order_drafts')
    assert provider.drafts.history('s', draft['draft_id'])['items'][0]['item_constraint'] == (
        draft['item_constraint']
    )


@pytest.mark.parametrize('customer', ['「客户甲」', '“客户甲”'])
async def test_quoted_customer_is_a_literal_name_not_a_quoted_creation_instruction(store, customer):
    provider, engine = store
    source = f'给{customer}准备订单：无线鼠标两件；只保存草稿'
    result, history = await invoke(provider, source)
    assert json.loads(result['content'])['customer'] == '客户甲'
    args = {'customer': '客户甲', 'items': [{'sku': 'M1', 'quantity': 2}]}
    saved, _ = await invoke(provider, source, 'prepare_order_draft', args, [history])
    assert saved['ok'] and len(snapshot_all(engine)['order_drafts']) == 1


@pytest.mark.parametrize("body,code", [
    ('标准耳机两件，机械键盘三件', 'ambiguous_product'),
    ('鼠标两件', 'product_not_found'), ('不存在的商品两件', 'product_not_found'),
    ('旧键盘两件', 'product_unavailable'), ('无线鼠标两件，SKU M1 三件', 'duplicate_product'),
    ('无线鼠标两箱', 'incomplete_request'), ('无线鼠标若干件', 'incomplete_request'),
    ('无线鼠标 2.5件', 'incomplete_request'), ('无线鼠标 -2件', 'incomplete_request'),
])
async def test_clarification_never_saves_a_partial_or_guessed_list(store, body, code):
    provider, engine = store
    text = f'给客户甲准备订单：{body}；只保存草稿'
    before = snapshot_all(engine)
    result, history = await invoke(provider, text)
    payload = json.loads(result['content'])
    assert payload['status'] == 'clarification_required' and payload['items'] == []
    assert code in {issue['code'] for issue in payload['issues']}
    guessed, _ = await invoke(provider, text, 'prepare_order_draft', ARGS, [history])
    assert not guessed['ok'] and snapshot_all(engine) == before


@pytest.mark.parametrize("fault", ['quantity', 'missing', 'order', 'extra', 'bool', 'customer'])
async def test_model_arguments_cannot_change_the_actual_resolved_requirements(store, fault):
    provider, engine = store
    _, history = await invoke(provider, TEXT)
    args = deepcopy(ARGS)
    if fault == 'quantity':
        args['items'][0]['quantity'] = 5
    elif fault == 'missing':
        args['items'].pop()
    elif fault == 'order':
        args['items'].reverse()
    elif fault == 'extra':
        args['items'].append({'sku': 'R1', 'quantity': 1})
    elif fault == 'bool':
        args['items'][0]['quantity'] = True
    else:
        args['customer'] = '缩写'
    before = snapshot_all(engine)
    result, _ = await invoke(provider, TEXT, 'prepare_order_draft', args, [history])
    assert not result['ok'] and snapshot_all(engine) == before


@pytest.mark.parametrize("change", ['duplicate', 'rename', 'off_sale'])
async def test_catalogue_is_rechecked_inside_the_save_transaction(store, change):
    provider, engine = store
    _, history = await invoke(provider, TEXT)
    evidence = validate_draft_arguments('prepare_order_draft', ARGS,
                                        [{'role': 'user', 'content': TEXT}], [history])
    with Session(engine) as session:
        if change == 'duplicate':
            session.add(ProductRow(sku='M2', name='无线鼠标', category='测试', price=100,
                                   status='在售'))
        else:
            product = session.get(ProductRow, 'M1')
            if change == 'rename':
                product.name = '另一款鼠标'
            else:
                product.status = '下架'
        session.commit()
    before = snapshot_all(engine)
    with pytest.raises(MutationError):
        provider.drafts.prepare('s', ARGS['customer'], [('M1', 2), ('K1', 3)], ARGS['note'],
                                budget_limit=1000, evidence=evidence)
    assert snapshot_all(engine) == before


async def test_no_previous_resolution_can_authorize_a_new_message(store):
    provider, engine = store
    before = snapshot_all(engine)
    result, _ = await invoke(provider, TEXT, 'prepare_order_draft', ARGS)
    assert not result['ok']
    _, history = await invoke(provider, TEXT)
    result, _ = await invoke(provider, '按刚才的来', 'prepare_order_draft', ARGS, [history])
    assert not result['ok'] and snapshot_all(engine) == before


async def test_parallel_prepare_cannot_use_a_resolution_that_has_not_returned(store):
    provider, engine = store
    tools = {tool.name: tool for tool in provider.for_session('s')}
    calls = prepare_tool_calls([
        ToolCall('read', 'resolve_order_items', '{}'),
        ToolCall('save', 'prepare_order_draft', json.dumps(ARGS)),
    ], tools, messages=[{'role': 'user', 'content': TEXT}])
    before = snapshot_all(engine)
    read = await execute_tool_call(calls[0], tools, LoopConfig())
    assert json.loads(read['content'])['status'] == 'resolved'
    save = await execute_tool_call(calls[1], tools, LoopConfig())
    assert not save['ok'] and snapshot_all(engine) == before


@pytest.mark.parametrize('source', [
    '如果有货，给客户甲准备订单：无线鼠标两件；只保存草稿',
    '只解释，给客户甲准备订单：无线鼠标两件；只保存草稿',
    '不要创建草稿：无线鼠标两件',
    '「给客户甲准备订单：无线鼠标两件；只保存草稿」',
    '备注原文「给客户甲准备订单：无线鼠标两件；只保存草稿」',
])
async def test_quotes_negation_and_conditions_are_not_natural_order_authorization(store, source):
    provider, engine = store
    before = snapshot_all(engine)
    result, history = await invoke(provider, source)
    assert json.loads(result['content'])['status'] == 'clarification_required'
    rejected, _ = await invoke(provider, source, 'prepare_order_draft', ARGS, [history])
    assert not rejected['ok'] and snapshot_all(engine) == before


@pytest.mark.parametrize('field', ['product_name', 'product_query', 'quantity_text', 'quote'])
async def test_forged_catalogue_proof_is_reconstructed_in_the_transaction(store, field):
    provider, engine = store
    _, history = await invoke(provider, TEXT)
    evidence = validate_draft_arguments('prepare_order_draft', ARGS,
                                        [{'role': 'user', 'content': TEXT}], [history])
    evidence['creation_guard']['requirements'][0][field] = '伪造'
    before = snapshot_all(engine)
    with pytest.raises(MutationError):
        provider.drafts.prepare('s', ARGS['customer'], [('M1', 2), ('K1', 3)], ARGS['note'],
                                budget_limit=1000, evidence=evidence)
    assert snapshot_all(engine) == before


async def test_duplicate_names_in_an_existing_draft_require_an_explicit_sku_or_row(store):
    provider, engine = store
    draft = provider.drafts.prepare('s', '客户甲', [('R1', 2), ('R2', 3)])
    args = {'draft_id': draft['draft_id'], 'expected_revision': 1, 'item_number': 1,
            'quantity': 5, 'expected_sku': 'R1'}
    history = [{'name': 'get_order_task', 'ok': True,
                'content': json.dumps(provider.drafts.task_state('s'))}]
    before = snapshot_all(engine)
    rejected, _ = await invoke(provider, '把商品标准耳机数量改为五件',
                               'revise_order_draft_quantity', args, history)
    assert not rejected['ok'] and snapshot_all(engine) == before


async def test_numeric_product_name_boundaries_and_sql_wildcards_are_not_guessed(store):
    _, engine = store
    with Session(engine) as session:
        for sku, name in [('V1', '维生素B'), ('V2', '维生素B十')]:
            session.add(ProductRow(sku=sku, name=name, category='测试', price=10, status='在售'))
        session.commit()
        result = resolve_request(session, '给客户甲准备订单：维生素B十二件；只保存草稿')
        assert result['issues'][0]['code'] == 'quantity_boundary_ambiguous'
        result = resolve_request(session, '给客户甲准备订单：十二件维生素B；只保存草稿')
        assert result['status'] == 'resolved' and result['items'] == [{'sku': 'V1', 'quantity': 12}]
        result = resolve_request(session, '给客户甲准备订单：%两件；只保存草稿')
        assert result['status'] == 'clarification_required'
        assert result['issues'][0]['candidates'] == []


class NaturalScript:
    def __init__(self):
        self.mistake = True

    async def stream_chat(self, messages, tools=None):
        start = max(i for i, m in enumerate(messages) if m['role'] == 'user')
        outputs = [json.loads(m['content']) for m in messages[start + 1:] if m['role'] == 'tool']
        resolved = next((o for o in outputs if 'source_user_sha256' in o), None)
        if resolved and resolved['status'] != 'resolved':
            yield TextDelta('标准耳机有 R1、R2 两个商品，请选择 SKU 并重新列出完整清单。')
        elif not any('draft_id' in o for o in outputs):
            args = deepcopy(ARGS) if resolved['customer'] == '客户甲' else None
            if args:
                args['items'] = resolved['items']
                if len(args['items']) == 1:
                    args.pop('note')
                    args.pop('budget_limit')
            if self.mistake:
                args = deepcopy(args)
                args['items'][0]['quantity'] += 1
                self.mistake = False
            yield ToolCall(f'create-{len(outputs)}', 'prepare_order_draft', json.dumps(args))
        else:
            yield TextDelta('草稿已保存，请核对完整商品、件数、金额和版本；未建单、未扣库存。')
        yield StreamEnd(Usage(100, 10, 110))


async def test_real_runtime_resolves_before_model_and_rejects_wrong_first_proposal(store):
    provider, engine = store
    agent = LangGraphRuntime(NaturalScript(), provider.for_session('s'),
                             checkpointer=InMemorySaver())
    events = [e async for e in agent.stream([{'role': 'user', 'content': TEXT}], thread_id='s')]
    assert events[-1].completed
    state = (await agent.get_state('s')).values
    assert [(r['name'], r['ok']) for r in state['tool_history']] == [
        ('resolve_order_items', True), ('prepare_order_draft', False),
        ('prepare_order_draft', True),
    ]
    assert len(snapshot_all(engine)['order_drafts']) == 1
    assert snapshot_all(engine)['orders'] == []


async def test_ambiguity_then_explicit_complete_sku_request_creates_only_correct_draft(store):
    provider, engine = store
    agent = LangGraphRuntime(NaturalScript(), provider.for_session('s'),
                             checkpointer=InMemorySaver())
    question = '给客户甲准备订单：标准耳机两件；只保存草稿'
    messages = [{'role': 'user', 'content': question}]
    _ = [e async for e in agent.stream(messages, thread_id='s')]
    state = (await agent.get_state('s')).values
    assert 'R1、R2' in state['final_answer'] and snapshot_all(engine)['order_drafts'] == []
    source = '给客户甲准备订单：SKU R2 两件；只保存草稿'
    _ = [e async for e in agent.stream([*state['messages'], {'role': 'user', 'content': source}],
                                     thread_id='s')]
    draft = provider.drafts.list_latest('s')[0]
    assert [(r['sku'], r['quantity']) for r in draft['items']] == [('R2', 2)]
    assert len(snapshot_all(engine)['order_drafts']) == 1


@pytest.mark.parametrize('instruction', ['把商品无线鼠标数量改为三件', '把当前版本第一行改成三件'])
async def test_name_or_row_and_chinese_quantity_preserve_other_lines(store, instruction):
    provider, engine = store
    draft = provider.drafts.prepare('s', '客户甲', [('M1', 2), ('K1', 3)])
    args = {'draft_id': draft['draft_id'], 'expected_revision': 1, 'item_number': 1,
            'quantity': 3, 'expected_sku': 'M1'}
    history = [{'name': 'get_order_task', 'ok': True,
                'content': json.dumps(provider.drafts.task_state('s'))}]
    result, _ = await invoke(provider, instruction, 'revise_order_draft_quantity', args, history)
    changed = json.loads(result['content'])
    assert result['ok'], result['content']
    assert [r['quantity'] for r in changed['items']] == [3, 3]
    assert changed['revision'] == 2 and snapshot_all(engine)['orders'] == []
