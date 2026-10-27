from pathlib import Path


def test_bound_private_datasets_are_visible_and_removable():
    src = Path("frontend/src/components/RagFlowResourceSelector.vue").read_text(encoding="utf-8")
    # 已绑定的私有库必须单独渲染出来，且能点掉
    assert "pinnedPrivateDatasets" in src
    body = src[src.index("pinnedPrivateDatasets = computed"):]
    body = body[: body.index("/** 已选中但仍留在配置里")]
    assert 'visibilityKey(ds) === "private"' in body
    assert "selectedIds.value.includes(ds.id)" in body

    tpl = src[src.index('v-for="ds in pinnedPrivateDatasets"'):]
    tpl = tpl[: tpl.index('v-for="ds in visibleDatasets"')]
    assert '@click="toggleSelection(ds.id)"' in tpl, "必须能解除绑定"
    assert "仅归属人可检索" in tpl
    # 未绑定的私有库仍然不能被新增
    assert 'v-for="ds in visibleDatasets"' in src
    listed = src[src.index('v-for="ds in visibleDatasets"'):]
    listed = listed[: listed.index("</template>")]
    assert "pinnedPrivateDatasets" not in listed
