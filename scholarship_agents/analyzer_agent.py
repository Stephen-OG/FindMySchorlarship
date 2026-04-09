from agents import Agent, AgentOutputSchema

from models.analyzer_model import AnalyzerResult
from utils.analyzer import analyze_crawler_results
from utils.constants import ANALYZER_INSTRUCTIONS
from utils.logger import logger

logger.info("Starting analyzer agent")

# Agent instructions
analyzer_instructions = ANALYZER_INSTRUCTIONS

# Create analyzer agent
analyzer_agent = Agent(
    name="Funding Analyzer",
    instructions=analyzer_instructions,
    tools=[analyze_crawler_results],
    model="gpt-4o-mini",
    output_type=AgentOutputSchema(AnalyzerResult, strict_json_schema=False),
)
