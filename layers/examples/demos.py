"""
Layers Examples & Demos
=======================

Demo functions extracted from deprecated coordination modules.
These are for documentation and testing purposes only.

Moved from:
- layers/hive_coordination.py (demo_connected_coordination)
- layers/smart_coordination.py (demo_smart_spawned_integration)
"""
import asyncio
import msgspec.json as _json
from datetime import UTC, datetime
from typing import Any

from hledac.universal.layers.hive_coordination import (
    ConnectedCoordinationSystem,
    TopologyType,
)
from hledac.universal.layers.smart_coordination import (
    SmartSpawnedCoordinationIntegration,
)


async def demo_connected_coordination() -> dict[str, Any]:
    """
    Demonstrate connected coordination system.

    This demo showcases:
    - Hive Mind collective intelligence
    - Auto-agent task analysis and spawning
    - Self-healing error recovery networks
    - Cognitive pattern mesh coordination
    - Cross-session memory persistence
    - Adaptive topology switching

    Returns:
        dict with demo results and final system status
    """
    system = ConnectedCoordinationSystem("swarm_1762976564550_bvsm9hm1n")

    print("🚀 Connected Coordination System Demo")
    print("=" * 50)
    status = system.get_system_status()
    print(f"System Status: {_json.encode(status)}")

    tasks = [
        "Research and analyze market trends for AI integration",
        "Design and implement a distributed computing system",
        "Review and optimize performance bottlenecks",
    ]

    results = []
    for i, task_desc in enumerate(tasks):
        print(f"\n📋 Processing Task {i + 1}: {task_desc}")
        task_id = await system.process_task(task_desc, "high")
        print(f"✅ Task {task_id} processed successfully")
        results.append({"task_id": task_id, "description": task_desc})

    print("\n🔄 Adapting topology to mesh for optimal parallel processing...")
    system.adapt_topology(TopologyType.MESH, "High parallel processing requirement")
    final_status = system.get_system_status()
    print(f"\n📊 Final System Status: {_json.encode(final_status)}")

    print("\n🎯 Connected Coordination System Integration Complete!")
    print("✅ Hive Mind collective intelligence integrated")
    print("✅ Auto-agent task analysis and spawning connected")
    print("✅ Self-healing networks established across all layers")
    print("✅ Cognitive pattern mesh coordination active")
    print("✅ Cross-session memory persistence enabled")
    print("✅ Adaptive topology switching operational")

    return {
        "demo": "connected_coordination",
        "results": results,
        "final_status": final_status,
        "completed_at": datetime.now(UTC).isoformat(),
    }


async def demo_smart_spawned_integration() -> dict[str, Any]:
    """
    Demonstrate the smart-spawned coordination integration.

    This demo showcases:
    - Smart coordinator for task optimization
    - Smart coders for implementation
    - Smart tester for validation
    - Performance analysis and recommendations

    Returns:
        dict with demo results and final status
    """
    connected_system = ConnectedCoordinationSystem("swarm_1762976564550_bvsm9hm1n")
    smart_integration = SmartSpawnedCoordinationIntegration(connected_system)

    print("🚀 Smart-Spawned Coordination Integration Demo")
    print("=" * 50)

    status = smart_integration.get_smart_coordination_status()
    print(f"Initial Smart Coordination Status: {_json.encode(status)}")

    tasks = [
        ("Implement intelligent task routing system", "high"),
        ("Optimize coordination layer performance", "medium"),
        ("Test cross-system integration capabilities", "high"),
    ]

    results = []
    for task_desc, priority in tasks:
        print(f"\n📋 Processing with Smart Coordination: {task_desc}")
        result = await smart_integration.process_task_with_smart_coordination(task_desc, priority)
        print(f"✅ Task {result['task_id']} completed")
        print(f"   Efficiency Score: {result['performance_analysis']['efficiency_score']:.2f}")
        print(f"   Processing Time: {result['total_processing_time']:.2f}s")
        results.append(result)

    final_status = smart_integration.get_smart_coordination_status()
    print(f"\n📊 Final Smart Coordination Status: {_json.encode(final_status)}")

    print("\n🎯 Smart-Spawned Integration Complete!")
    print("✅ Smart coordinator optimized task analysis and routing")
    print("✅ Smart coders implemented components efficiently")
    print("✅ Smart tester validated integration comprehensively")
    print("✅ Performance analysis provided actionable insights")
    print("✅ System recommendations generated for optimization")

    return {
        "demo": "smart_spawned_integration",
        "results": results,
        "final_status": final_status,
        "completed_at": datetime.now(UTC).isoformat(),
    }


async def run_all_demos() -> dict[str, Any]:
    """Run all layer demos."""
    results = {}

    print("\n" + "=" * 70)
    print("LAYERS EXAMPLES & DEMOS")
    print("=" * 70 + "\n")

    results["connected_coordination"] = await demo_connected_coordination()

    print("\n" + "-" * 70 + "\n")

    results["smart_spawned_integration"] = await demo_smart_spawned_integration()

    print("\n" + "=" * 70)
    print("ALL DEMOS COMPLETED")
    print("=" * 70)

    return results


if __name__ == "__main__":
    asyncio.run(run_all_demos())
