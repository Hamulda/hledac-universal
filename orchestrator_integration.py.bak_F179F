"""
Orchestrator Integration Extension - Phase C Full Autonomy
===========================================================

This module extends FullyAutonomousOrchestrator with:
1. Connected coordinators (MetaReasoning, Memory, Security, Monitoring, Validation)
2. Real data sources (ArXiv, GitHub APIs)
3. AI-driven decision making
4. Deep knowledge graph integration
5. Self-monitoring and auto-recovery

Usage:
    from hledac.universal.orchestrator_integration import IntegratedOrchestrator
    
    orchestrator = IntegratedOrchestrator()
    await orchestrator.initialize()
    result = await orchestrator.research("your query", DiscoveryDepth.EXHAUSTIVE)
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional
import aiohttp
from datetime import datetime

# Import base orchestrator
from .autonomous_orchestrator import (
    FullyAutonomousOrchestrator,
    DiscoveryDepth,
    ResearchPhase,
    SourceType,
    AutonomousStrategy,
    ResearchFinding,
    ResearchSource,
    ComprehensiveResearchResult,
)
from .types import ResearchMode, OrchestratorState, PrivacyLevel
from .coordinators.agent_coordination_engine import AgentType

# Try to import coordinators
try:
    from .coordinators.meta_reasoning_coordinator import UniversalMetaReasoningCoordinator
    META_REASONING_AVAILABLE = True
except ImportError:
    UniversalMetaReasoningCoordinator = None
    META_REASONING_AVAILABLE = False

try:
    from .coordinators.memory_coordinator import UniversalMemoryCoordinator
    MEMORY_COORD_AVAILABLE = True
except ImportError:
    UniversalMemoryCoordinator = None
    MEMORY_COORD_AVAILABLE = False

try:
    from .coordinators.security_coordinator import UniversalSecurityCoordinator
    SECURITY_COORD_AVAILABLE = True
except ImportError:
    UniversalSecurityCoordinator = None
    SECURITY_COORD_AVAILABLE = False

try:
    from .coordinators.monitoring_coordinator import UniversalMonitoringCoordinator
    MONITORING_AVAILABLE = True
except ImportError:
    UniversalMonitoringCoordinator = None
    MONITORING_AVAILABLE = False

try:
    from .coordinators.validation_coordinator import UniversalValidationCoordinator
    VALIDATION_AVAILABLE = True
except ImportError:
    UniversalValidationCoordinator = None
    VALIDATION_AVAILABLE = False

try:
    from .coordinators.swarm_coordinator import UniversalSwarmCoordinator
    SWARM_AVAILABLE = True
except ImportError:
    UniversalSwarmCoordinator = None
    SWARM_AVAILABLE = False

try:
    from .coordinators.quantum_coordinator import UniversalQuantumCoordinator
    QUANTUM_AVAILABLE = True
except ImportError:
    UniversalQuantumCoordinator = None
    QUANTUM_AVAILABLE = False

logger = logging.getLogger(__name__)


class IntegratedOrchestrator(FullyAutonomousOrchestrator):
    """
    Extended orchestrator with full coordinator integration and real data sources.
    
    This class extends FullyAutonomousOrchestrator to provide:
    - Connected coordinators for advanced capabilities
    - Real academic search via ArXiv API
    - Real OSINT via GitHub API
    - AI-driven autonomous decision making
    - Self-monitoring and validation
    """
    
    def __init__(self, config=None):
        super().__init__(config)
        
        # Connect coordinators
        self.meta_reasoning = UniversalMetaReasoningCoordinator() if META_REASONING_AVAILABLE else None
        self.memory_coord = UniversalMemoryCoordinator() if MEMORY_COORD_AVAILABLE else None
        self.security_coord = UniversalSecurityCoordinator() if SECURITY_COORD_AVAILABLE else None
        self.monitoring = UniversalMonitoringCoordinator() if MONITORING_AVAILABLE else None
        self.validator = UniversalValidationCoordinator() if VALIDATION_AVAILABLE else None
        self.swarm = UniversalSwarmCoordinator() if SWARM_AVAILABLE else None
        self.quantum = UniversalQuantumCoordinator() if QUANTUM_AVAILABLE else None
        
        # HTTP session for real API calls
        self._http_session: Optional[aiohttp.ClientSession] = None
        
        logger.info("🔧 IntegratedOrchestrator initialized")
        logger.info(f"   Coordinators: meta={META_REASONING_AVAILABLE}, memory={MEMORY_COORD_AVAILABLE}, "
                   f"security={SECURITY_COORD_AVAILABLE}, monitoring={MONITORING_AVAILABLE}, "
                   f"validation={VALIDATION_AVAILABLE}, swarm={SWARM_AVAILABLE}, quantum={QUANTUM_AVAILABLE}")
    
    async def initialize(self) -> bool:
        """Initialize orchestrator with all coordinators."""
        # Initialize base orchestrator
        success = await super().initialize()
        if not success:
            return False
        
        # Initialize HTTP session
        self._http_session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30),
            headers={'User-Agent': 'Hledac-Research/1.0'}
        )
        
        # Initialize coordinators
        init_tasks = []
        
        if self.meta_reasoning:
            init_tasks.append(self._init_coordinator("meta_reasoning", self.meta_reasoning))
        if self.memory_coord:
            init_tasks.append(self._init_coordinator("memory_coord", self.memory_coord))
        if self.security_coord:
            init_tasks.append(self._init_coordinator("security_coord", self.security_coord))
        if self.monitoring:
            init_tasks.append(self._init_coordinator("monitoring", self.monitoring))
        if self.validator:
            init_tasks.append(self._init_coordinator("validator", self.validator))
        if self.swarm:
            init_tasks.append(self._init_coordinator("swarm", self.swarm))
        if self.quantum:
            init_tasks.append(self._init_coordinator("quantum", self.quantum))
        
        if init_tasks:
            results = await asyncio.gather(*init_tasks, return_exceptions=True)
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.warning(f"Coordinator {i} failed to initialize: {result}")
        
        logger.info("✅ IntegratedOrchestrator fully initialized")
        return True
    
    async def _init_coordinator(self, name: str, coordinator) -> None:
        """Initialize a single coordinator with error handling."""
        try:
            if hasattr(coordinator, 'initialize'):
                await coordinator.initialize()
            logger.info(f"   ✓ {name} coordinator initialized")
        except Exception as e:
            logger.warning(f"   ✗ {name} coordinator failed: {e}")
            raise
    
    async def cleanup(self) -> None:
        """Cleanup all resources."""
        # Close HTTP session
        if self._http_session:
            await self._http_session.close()
        
        # Cleanup coordinators
        if self.meta_reasoning and hasattr(self.meta_reasoning, 'cleanup'):
            await self.meta_reasoning.cleanup()
        if self.monitoring and hasattr(self.monitoring, 'cleanup'):
            await self.monitoring.cleanup()
        
        # Call base cleanup
        await super().cleanup()
    
    # =============================================================================
    # REAL DATA SOURCES - Replacing Mock Implementations
    # =============================================================================
    
    async def _execute_academic_search(self, query: str) -> Dict[str, Any]:
        """
        REAL academic search via ArXiv API.
        
        Replaces mock implementation with actual ArXiv API calls.
        """
        findings = []
        sources = []
        
        try:
            # ArXiv API endpoint
            url = "http://export.arxiv.org/api/query"
            params = {
                'search_query': f'all:{query}',
                'start': 0,
                'max_results': 10,
                'sortBy': 'relevance',
                'sortOrder': 'descending'
            }
            
            async with self._http_session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.text()
                    papers = self._parse_arxiv_response(data)
                    
                    for paper in papers:
                        source = ResearchSource(
                            url=paper['url'],
                            title=paper['title'],
                            content=paper['summary'][:500] if paper['summary'] else '',
                            source_type=SourceType.ACADEMIC,
                            confidence=0.85,
                            metadata={
                                'authors': paper['authors'],
                                'published': paper['published'],
                                'primary_category': paper['primary_category']
                            }
                        )
                        sources.append(source)
                        findings.append(ResearchFinding(
                            content=paper['summary'][:500] if paper['summary'] else paper['title'],
                            source=source,
                            confidence=0.85,
                            category='evidence'
                        ))
                    
                    logger.info(f"   ✓ ArXiv search: {len(papers)} papers found")
                else:
                    logger.warning(f"   ✗ ArXiv API error: {response.status}")
        
        except Exception as e:
            logger.warning(f"   ✗ Academic search failed: {e}")
            # Fallback to base implementation if available
            return await super()._execute_academic_search(query)
        
        return {'findings': findings, 'sources': sources}
    
    def _parse_arxiv_response(self, xml_data: str) -> List[Dict[str, Any]]:
        """Parse ArXiv XML response."""
        import xml.etree.ElementTree as ET
        
        papers = []
        try:
            root = ET.fromstring(xml_data)
            # ArXiv uses Atom namespace
            ns = {'atom': 'http://www.w3.org/2005/Atom'}
            
            for entry in root.findall('atom:entry', ns):
                paper = {
                    'title': '',
                    'summary': '',
                    'url': '',
                    'authors': [],
                    'published': '',
                    'primary_category': ''
                }
                
                title_elem = entry.find('atom:title', ns)
                if title_elem is not None:
                    paper['title'] = title_elem.text.strip() if title_elem.text else ''
                
                summary_elem = entry.find('atom:summary', ns)
                if summary_elem is not None:
                    paper['summary'] = summary_elem.text.strip() if summary_elem.text else ''
                
                id_elem = entry.find('atom:id', ns)
                if id_elem is not None:
                    paper['url'] = id_elem.text.strip() if id_elem.text else ''
                
                published_elem = entry.find('atom:published', ns)
                if published_elem is not None:
                    paper['published'] = published_elem.text.strip() if published_elem.text else ''
                
                # Get authors
                for author in entry.findall('atom:author', ns):
                    name_elem = author.find('atom:name', ns)
                    if name_elem is not None and name_elem.text:
                        paper['authors'].append(name_elem.text.strip())
                
                # Get primary category
                cat_elem = entry.find('atom:category', ns)
                if cat_elem is not None:
                    paper['primary_category'] = cat_elem.get('term', '')
                
                papers.append(paper)
        
        except Exception as e:
            logger.error(f"Failed to parse ArXiv response: {e}")
        
        return papers
    
    async def _execute_osint_search(self, query: str) -> Dict[str, Any]:
        """
        REAL OSINT search via GitHub API.
        
        Replaces mock implementation with actual GitHub API calls.
        """
        findings = []
        sources = []
        
        try:
            # GitHub API endpoint
            url = "https://api.github.com/search/repositories"
            params = {
                'q': query,
                'sort': 'stars',
                'order': 'desc',
                'per_page': 10
            }
            
            async with self._http_session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    repos = data.get('items', [])
                    
                    for repo in repos:
                        source = ResearchSource(
                            url=repo.get('html_url', ''),
                            title=repo.get('full_name', ''),
                            content=repo.get('description', '') or f"Repository for {query}",
                            source_type=SourceType.OSINT,
                            confidence=0.7,
                            metadata={
                                'stars': repo.get('stargazers_count', 0),
                                'language': repo.get('language', 'unknown'),
                                'updated_at': repo.get('updated_at', '')
                            }
                        )
                        sources.append(source)
                        findings.append(ResearchFinding(
                            content=repo.get('description', f"GitHub repo: {repo.get('full_name', '')}"),
                            source=source,
                            confidence=0.7,
                            category='fact'
                        ))
                    
                    logger.info(f"   ✓ GitHub search: {len(repos)} repos found")
                else:
                    logger.warning(f"   ✗ GitHub API error: {response.status}")
        
        except Exception as e:
            logger.warning(f"   ✗ OSINT search failed: {e}")
        
        return {'findings': findings, 'sources': sources}
    
    # =============================================================================
    # AI-DRIVEN DECISION MAKING
    # =============================================================================
    
    async def _analyze_query(self, query: str, language: str) -> Dict[str, Any]:
        """
        AI-powered query analysis using Hermes-3.
        
        Replaces rule-based analysis with LLM-based intent detection.
        """
        if not self.hermes:
            # Fallback to base implementation
            return await super()._analyze_query(query, language)
        
        try:
            system_msg = "You analyze research queries. Respond ONLY in JSON."
            
            prompt = f"""Analyze this research query and provide structured information:

Query: "{query}"
Language: {language}

Analyze:
1. Intent (academic, technical, historical, news, general)
2. Key entities (names, organizations, concepts)
3. Complexity (simple, moderate, complex)
4. Suggested research depth (surface, deep, extreme)

Return ONLY this JSON:
{{
  "intent": "intent_type",
  "entities": ["entity1", "entity2"],
  "complexity": "complexity_level",
  "suggested_depth": "depth_level",
  "reasoning": "brief explanation"
}}"""
            
            response = await self.hermes.generate(
                prompt=prompt,
                system_msg=system_msg,
                temperature=0.3,
                max_tokens=512
            )
            
            # Parse JSON response
            analysis = self._parse_json_response(response)
            
            if analysis:
                return {
                    'intent': analysis.get('intent', 'general'),
                    'entities': analysis.get('entities', []),
                    'complexity': analysis.get('complexity', 'moderate'),
                    'suggested_depth': analysis.get('suggested_depth', 'deep'),
                    'reasoning': analysis.get('reasoning', ''),
                    'language': language
                }
        
        except Exception as e:
            logger.warning(f"AI query analysis failed: {e}")
        
        # Fallback to base implementation
        return await super()._analyze_query(query, language)
    
    def _parse_json_response(self, response: str) -> Optional[Dict[str, Any]]:
        """Parse JSON from LLM response."""
        try:
            text = response.strip()
            
            # Remove markdown code blocks
            if text.startswith('```json'):
                text = text[7:]
            elif text.startswith('```'):
                text = text[3:]
            if text.endswith('```'):
                text = text[:-3]
            
            text = text.strip()
            
            # Find JSON object
            start_idx = text.find('{')
            end_idx = text.rfind('}')
            
            if start_idx >= 0 and end_idx > start_idx:
                json_str = text[start_idx:end_idx+1]
                return json.loads(json_str)
        
        except Exception as e:
            logger.debug(f"Failed to parse JSON response: {e}")
        
        return None
    
    # =============================================================================
    # COORDINATOR INTEGRATION
    # =============================================================================
    
    async def research_with_meta_reasoning(
        self,
        query: str,
        depth: DiscoveryDepth = DiscoveryDepth.DEEP
    ) -> ComprehensiveResearchResult:
        """
        Research with meta-reasoning self-reflection.
        
        Uses MetaReasoningCoordinator to analyze and improve reasoning process.
        """
        if not self.meta_reasoning:
            logger.warning("Meta-reasoning not available, using standard research")
            return await self.research(query, depth)
        
        logger.info("🧠 Starting research with meta-reasoning...")
        
        # Start reasoning chain
        reasoning_chain = []
        
        # Initial reasoning
        initial_reasoning = await self.meta_reasoning.analyze_reasoning_step(
            query=query,
            context={},
            step_number=1
        )
        reasoning_chain.append(initial_reasoning)
        
        # Execute standard research
        result = await self.research(query, depth)
        
        # Reflect on results
        reflection = await self.meta_reasoning.reflect_on_outcome(
            query=query,
            findings=result.findings,
            reasoning_chain=reasoning_chain
        )
        
        # If reflection suggests gaps, do additional research
        if reflection.get('gaps_identified') and depth.value < DiscoveryDepth.EXHAUSTIVE.value:
            logger.info("   ↻ Meta-reasoning identified gaps, conducting additional research...")
            
            for gap in reflection['gaps_identified'][:2]:  # Limit to 2 additional searches
                additional_result = await self.research(gap, DiscoveryDepth.SURFACE)
                result.findings.extend(additional_result.findings)
                result.sources.extend(additional_result.sources)
        
        # Add meta-reasoning insights to result
        result.statistics['meta_reasoning'] = {
            'reasoning_chain_length': len(reasoning_chain),
            'reflection': reflection.get('summary', ''),
            'confidence': reflection.get('confidence', 0.5)
        }
        
        return result
    
    async def research_with_swarm(
        self,
        query: str,
        depth: DiscoveryDepth = DiscoveryDepth.DEEP
    ) -> ComprehensiveResearchResult:
        """
        Research with swarm coordination for parallel exploration.
        
        Uses SwarmCoordinator to distribute research across multiple agents.
        """
        if not self.swarm:
            logger.warning("Swarm coordination not available, using standard research")
            return await self.research(query, depth)
        
        logger.info("🐝 Starting swarm-coordinated research...")
        
        # Initialize swarm
        await self.swarm.initialize_swarm(query)
        
        # Create sub-queries for swarm agents
        sub_queries = await self._generate_sub_queries(query)
        
        # Execute swarm exploration
        swarm_results = await self.swarm.coordinated_exploration(
            queries=sub_queries,
            depth=depth
        )
        
        # Aggregate results
        all_findings = []
        all_sources = []
        
        for agent_result in swarm_results:
            all_findings.extend(agent_result.get('findings', []))
            all_sources.extend(agent_result.get('sources', []))
        
        # Synthesize final report
        report = await self._synthesize_report(
            query=query,
            findings=all_findings,
            sources=all_sources,
            insights=None,
            temporal_analysis=None,
            language='en'
        )
        
        return ComprehensiveResearchResult(
            query=query,
            strategy=AutonomousStrategy(
                depth=depth,
                selected_sources=list(set(s.source_type for s in all_sources)),
                selected_agents=[AgentType.GENERAL, AgentType.ACADEMIC, AgentType.OSINT],
                optimization=self.optimizer.config.strategy if self.optimizer else None,
                privacy_level=PrivacyLevel.STANDARD,
                use_archive_mining=True,
                use_temporal_analysis=False,
                use_steganography=False,
                use_osint=True,
                parallel_execution=True,
                reasoning="Swarm-coordinated parallel exploration"
            ),
            findings=all_findings,
            sources=all_sources,
            synthesized_report=report,
            execution_time=0,  # Would track actual time
            total_sources_checked=len(all_sources),
            confidence_score=self._calculate_overall_confidence(all_findings, all_sources),
            statistics={
                'swarm_agents': len(swarm_results),
                'sub_queries': len(sub_queries)
            }
        )
    
    async def _generate_sub_queries(self, query: str) -> List[str]:
        """Generate sub-queries for swarm exploration."""
        if self.hermes:
            prompt = f"""Break down this research query into 3-5 specific sub-queries for parallel exploration.

Query: "{query}"

Return ONLY a JSON array of sub-queries:
["sub-query 1", "sub-query 2", ...]"""
            
            try:
                response = await self.hermes.generate(prompt, max_tokens=512)
                sub_queries = self._parse_json_response(response)
                if isinstance(sub_queries, list):
                    return sub_queries[:5]  # Limit to 5
            except Exception as e:
                logger.warning(f"Failed to generate sub-queries: {e}")
        
        # Fallback: use query expansion
        return [query] + self.expander.expand(query)[:4]
    
    async def research_with_validation(
        self,
        query: str,
        depth: DiscoveryDepth = DiscoveryDepth.DEEP
    ) -> ComprehensiveResearchResult:
        """
        Research with output validation.
        
        Uses ValidationCoordinator to check result quality.
        """
        # Execute research
        result = await self.research(query, depth)
        
        if not self.validator:
            return result
        
        logger.info("🔍 Validating research output...")
        
        # Validate findings
        validation = await self.validator.validate_research_output(
            query=query,
            findings=result.findings,
            sources=result.sources,
            report=result.synthesized_report
        )
        
        # If validation fails, retry with corrections
        if not validation.get('is_valid', True):
            logger.warning(f"   ✗ Validation failed: {validation.get('issues', [])}")
            
            # Try to fix issues
            for issue in validation.get('issues', [])[:2]:
                if issue.get('type') == 'insufficient_sources':
                    # Do additional search
                    additional = await self.research(query, DiscoveryDepth.SURFACE)
                    result.findings.extend(additional.findings)
                    result.sources.extend(additional.sources)
        
        # Add validation info to result
        result.statistics['validation'] = {
            'is_valid': validation.get('is_valid', True),
            'score': validation.get('score', 0.5),
            'issues_count': len(validation.get('issues', []))
        }
        
        return result
    
    # =============================================================================
    # SELF-MONITORING
    # =============================================================================
    
    async def get_system_health(self) -> Dict[str, Any]:
        """Get comprehensive system health status."""
        health = {
            'timestamp': datetime.now().isoformat(),
            'orchestrator_state': self.state.value,
            'phase': self.phase.name,
            'initialized': self._initialized,
            'execution_count': self._execution_count
        }
        
        # Memory status
        if self.memory_coord:
            try:
                mem_stats = await self.memory_coord.get_memory_stats()
                health['memory'] = mem_stats
            except Exception as e:
                health['memory_error'] = str(e)
        
        # Monitoring status
        if self.monitoring:
            try:
                monitor_stats = await self.monitoring.get_system_metrics()
                health['monitoring'] = monitor_stats
            except Exception as e:
                health['monitoring_error'] = str(e)
        
        return health


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

async def integrated_research(
    query: str,
    depth: str = "deep",
    use_meta_reasoning: bool = False,
    use_swarm: bool = False,
    use_validation: bool = True
) -> ComprehensiveResearchResult:
    """
    Convenience function for integrated research with all features.
    
    Args:
        query: Research query
        depth: 'surface', 'deep', 'extreme', or 'exhaustive'
        use_meta_reasoning: Enable meta-reasoning self-reflection
        use_swarm: Enable swarm coordination
        use_validation: Enable output validation
    
    Returns:
        Comprehensive research result
    """
    depth_map = {
        'surface': DiscoveryDepth.SURFACE,
        'deep': DiscoveryDepth.DEEP,
        'extreme': DiscoveryDepth.EXTREME,
        'exhaustive': DiscoveryDepth.EXHAUSTIVE
    }
    
    depth_enum = depth_map.get(depth.lower(), DiscoveryDepth.DEEP)
    
    orchestrator = IntegratedOrchestrator()
    
    try:
        await orchestrator.initialize()
        
        # Select research method based on options
        if use_swarm:
            result = await orchestrator.research_with_swarm(query, depth_enum)
        elif use_meta_reasoning:
            result = await orchestrator.research_with_meta_reasoning(query, depth_enum)
        elif use_validation:
            result = await orchestrator.research_with_validation(query, depth_enum)
        else:
            result = await orchestrator.research(query, depth_enum)
        
        return result
    
    finally:
        await orchestrator.cleanup()


__all__ = [
    'IntegratedOrchestrator',
    'integrated_research',
]
