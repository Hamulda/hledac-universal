"""Helper script to refactor _tree_of_thoughts_reasoning function."""
import re

file_path = '/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/coordinators/meta_reasoning_coordinator.py'
with open(file_path, 'r') as f:
    content = f.read()

# Find the function
start = content.find('async def _tree_of_thoughts_reasoning(self, query: str)')
if start == -1:
    print("Function not found!")
    exit(1)

# Find the end - next method at same indentation level
rest = content[start:]
next_method = re.search(r'\n    async def [a-zA-Z_]', rest[10:])
next_method2 = re.search(r'\n    def [a-zA-Z_]', rest[10:])
end_match = None
if next_method and next_method2:
    end_match = min(next_method.start(), next_method2.start())
elif next_method:
    end_match = next_method.start()
elif next_method2:
    end_match = next_method2.start()

if end_match is None:
    print("Could not find end of function")
    exit(1)

end = start + 10 + end_match
old_function = content[start:end]
print(f"Found function of length {len(old_function)}")

new_code = '''async def _tree_of_thoughts_reasoning(self, query: str) -> dict[str, Any]:
        """Execute Tree of Thoughts reasoning.

        BLITZ-04: Urgency-aware — parameters (max_depth, branching_factor,
        beam_width) are dynamically clamped via _clamp_tot_config().
        When urgency >= _URGENCY_TOT_SKIP (last ~5 min), returns immediately
        with a CoT fallback recommendation (caller handles fallback).

        SOVEREIGN-005 enhancements:
        - Learned value prediction via AdaptiveCostModel (replaces random estimates)
        - Cost-weighted pruning: branches with gain < 0.1 after 2+ actions are pruned
        - Dead-end detection: branches stalled for 10s without IOC progress are terminated

        UNIFIED-005: Periodic checkpointing via TransactionalToTCheckpointer.
        """
        config = self._clamp_tot_config(self.strategy_configs[ReasoningStrategy.TREE_OF_THOUGHTS])
        max_depth, branching_factor, beam_width = config['max_depth'], config['branching_factor'], config['beam_width']
        
        if max_depth == 0:
            return _tot_urgency_skip(self._compute_urgency())
        
        checkpointer = await _init_tot_checkpointer(self, query)
        value_predictor = _TotValuePredictor(cost_model=self._cost_model)
        dead_end_detector = _DeadEndDetector(timeout_s=_DEAD_END_TIMEOUT_S)
        query_complexity = min(len(set(query.split())) / 20.0, 1.0)
        
        nodes, leaves, _resumed = await _resume_or_create_root(self, checkpointer)
        
        branching_factor = await _apply_gravity_void_boost(self, branching_factor, query)
        
        if checkpointer:
            checkpointer._step = self._resume_step if _resumed else 0
            checkpointer.bind(nodes)
            await checkpointer.start()
            await checkpointer.checkpoint(nodes=nodes, step=checkpointer._step)
        
        result = await _run_tot_search(
            self, query, nodes, leaves, max_depth, branching_factor, beam_width,
            value_predictor, dead_end_detector, query_complexity, checkpointer
        )
        
        if checkpointer:
            try:
                await checkpointer.checkpoint(nodes=nodes)
                await checkpointer.stop(final_checkpoint=False)
            except Exception:
                pass
        
        return result


async def _init_tot_checkpointer(self, query: str):
    """Initialize ToT checkpointer if duckdb_store is available."""
    from msgspec import to_builtins as _to_builtins
    if self._duckdb_store is not None and self._sprint_id is not None:
        try:
            from hledac.universal.coordinators.tot_checkpointer import TransactionalToTCheckpointer
            checkpointer = TransactionalToTCheckpointer(
                sprint_id=self._sprint_id, duckdb_store=self._duckdb_store,
                interval_s=30.0, query_hash=self._query_hash,
            )
            self._checkpointer = checkpointer
            return checkpointer
        except Exception:
            logger.debug('ToT checkpointer init failed')
    return None


def _tot_urgency_skip(urgency: float) -> dict[str, Any]:
    """Return urgency skip result."""
    return {
        'type': 'tree_of_thoughts_skipped_urgency', 'nodes': 0, 'depth': 0, 'best_path': [],
        'best_value': 0.0, 'pruned_branches': 0, 'dead_ends': 0, 'used_learned_values': False,
        'urgency': urgency, 'fallback_recommended': 'chain_of_thought',
        'summary': 'ToT skipped due to high urgency — use CoT with max_steps=3',
    }


async def _resume_or_create_root(self, checkpointer):
    """Resume from checkpoint or create new root node."""
    if self._resume_from is not None and self._resume_step > 0:
        nodes = dict(self._resume_from)
        root_candidates = [n for n in nodes.values() if n.depth == 0]
        root = root_candidates[0] if root_candidates else next(iter(nodes.values()))
        max_depth = max((n.depth for n in nodes.values()), default=0)
        leaves = [n for n in nodes.values() if not n.expanded and n.depth == max_depth] or [root]
        logger.info('[UNIFIED-006] ToT resumed: step=%d nodes=%d leaves=%d', self._resume_step, len(nodes), len(leaves))
        return nodes, leaves, True
    
    root = ThoughtNode(node_id='root', thought=f'Exploring: {self._query[:50]}...', value_estimate=0.5, depth=0, cost=0.0, uncertainty=0.0)
    nodes = {'root': root}
    if checkpointer:
        from msgspec import to_builtins as _to_builtins
        await checkpointer.incremental_checkpoint('root', _to_builtins(root), step=0)
    return nodes, [root], False


async def _apply_gravity_void_boost(self, branching_factor: int, query: str) -> int:
    """Apply gravity void exploration bonus to branching factor."""
    if self._gravity_field is None:
        return branching_factor
    try:
        voids = self._gravity_field.find_voids(k=5, min_distance=0.25)
        if voids:
            max_radius = max(v.radius for v in voids)
            bonus = min(0.15, 0.02 * len(voids) * (1.0 + max_radius))
            if bonus > 0:
                logger.debug('[SILICON-05] ToT exploration bonus=%.3f', bonus)
            if len(voids) >= 3:
                branching_factor = min(branching_factor + 1, 6)
                logger.debug('[SILICON-05] ToT branching boosted to %d', branching_factor)
            return branching_factor
    except Exception:
        logger.debug('[SILICON-05] Gravity void query failed')
    return branching_factor


async def _run_tot_search(
    self, query, nodes, leaves, max_depth, branching_factor, beam_width,
    value_predictor, dead_end_detector, query_complexity, checkpointer
):
    """Run the main ToT search loop."""
    best_value, pruned_count, dead_end_count, igd_count = float('-inf'), 0, 0, 0
    best_path = []
    nodes_since_yield = 0
    
    dead_end_detector.register_branch('root')
    self._igd_policy.register_branch('root')
    
    for depth in range(max_depth):
        new_leaves = []
        for leaf in leaves:
            if leaf.expanded:
                continue
            if self._igd_policy.should_abort(leaf.node_id, depth=leaf.depth):
                igd_count += 1
                leaf.expanded = True
                continue
            if dead_end_detector.is_dead_end(leaf.node_id):
                dead_end_count += 1
                leaf.expanded = True
                continue
            
            new_leaves = await _expand_branches(
                self, leaf, depth, branching_factor, value_predictor, query_complexity,
                nodes, dead_end_detector, checkpointer, pruned_count
            )
            leaf.expanded = True
            nodes_since_yield = await _yield_if_needed(nodes_since_yield)
        
        leaves = _select_beam(new_leaves, beam_width)
        _cleanup_detectors(dead_end_detector, self._igd_policy, leaves)
        
        if checkpointer:
            await checkpointer.checkpoint(nodes=nodes, step=depth + 1)
        
        best_path, best_value = _find_best_path(nodes, leaves, best_path, best_value)
    
    return _build_tot_result(
        nodes, best_path, best_value, pruned_count, dead_end_count, igd_count,
        value_predictor, self._resume_step if self._resume_from else 0, self._resume_from is not None
    )


async def _expand_branches(self, leaf, depth, branching_factor, value_predictor, query_complexity, nodes, dead_end_detector, checkpointer, pruned_count):
    """Expand leaf node into branches."""
    new_leaves = []
    parent_value = leaf.value_estimate
    
    for i in range(branching_factor):
        child_id = f'node_{depth}_{i}_{leaf.node_id}'
        value_est, uncertainty = value_predictor.predict_value(child_id, depth + 1, parent_value, query_complexity)
        
        child = ThoughtNode(node_id=child_id, thought=f'Branch {i + 1} at depth {depth + 1}', value_estimate=value_est, parent=leaf.node_id, depth=depth + 1, cost=leaf.cost + 1.0, uncertainty=uncertainty)
        gain = value_est - parent_value
        
        if depth >= _PRUNE_MIN_DEPTH and gain < _PRUNE_GAIN_THRESHOLD:
            pruned_count += 1
            child.thought = f'Pruned branch {i + 1} at depth {depth + 1} (gain={gain:.3f})'
            nodes[child_id] = child
            leaf.children.append(child_id)
            if checkpointer:
                from msgspec import to_builtins as _to_builtins
                await checkpointer.incremental_checkpoint(child_id, _to_builtins(child), step=depth + 1)
            continue
        
        leaf.children.append(child_id)
        nodes[child_id] = child
        new_leaves.append(child)
        
        if checkpointer:
            from msgspec import to_builtins as _to_builtins
            await checkpointer.incremental_checkpoint(child_id, _to_builtins(child), step=depth + 1)
        
        self._igd_policy.register_branch(child_id)
        dead_end_detector.register_branch(child_id)
        
        if value_est > 0.5:
            dead_end_detector.report_progress(child_id, ioc_count=1)
            self._igd_policy.report_iocs(child_id, [value_est])
    
    return new_leaves


async def _yield_if_needed(nodes_since_yield: int) -> int:
    """Yield to event loop periodically."""
    nodes_since_yield += 1
    if nodes_since_yield >= _YIELD_EVERY_TOT:
        await asyncio.sleep(0)
        return 0
    return nodes_since_yield


def _select_beam(new_leaves: list, beam_width: int) -> list:
    """Select top leaves for beam width."""
    if len(new_leaves) > beam_width:
        new_leaves.sort(key=lambda n: n.value_estimate - 0.05 * n.cost, reverse=True)
        return new_leaves[:beam_width]
    return new_leaves


def _cleanup_detectors(dead_end_detector, igd_policy, leaves):
    """Cleanup detectors for pruned branches."""
    active_ids = {n.node_id for n in leaves} | {'root'}
    dead_end_detector.cleanup_pruned(active_ids)
    igd_policy.cleanup_pruned(active_ids)


def _find_best_path(nodes: dict, leaves: list, best_path: list, best_value: float) -> tuple:
    """Find best path through the tree."""
    for leaf in leaves:
        if leaf.value_estimate > best_value:
            best_value = leaf.value_estimate
            path = [leaf.node_id]
            current = leaf
            while current.parent:
                path.append(current.parent)
                current = nodes[current.parent]
            best_path = list(reversed(path))
    return best_path, best_value


def _build_tot_result(nodes, best_path, best_value, pruned_count, dead_end_count, igd_count, value_predictor, resume_step, resumed):
    """Build final ToT result."""
    return {
        'type': 'tree_of_thoughts', 'nodes': len(nodes), 'depth': len(best_path),
        'best_path': best_path, 'best_value': best_value,
        'pruned_branches': pruned_count, 'dead_ends': dead_end_count, 'igd_aborts': igd_count,
        'used_learned_values': value_predictor.is_learned,
        'resumed': resumed, 'resume_step': resume_step,
        'igd_policy_stats': {},  # Simplified
        'summary': f"ToT: {len(nodes)} nodes, {pruned_count} pruned, {dead_end_count} dead-ends, {igd_count} IGD-aborts, learned={value_predictor.is_learned}{', RESUMED' if resumed else ''}",
    }


'''
new_content = content[:start] + new_code + content[end:]

with open(file_path, 'w') as f:
    f.write(new_content)

print("File updated successfully")
