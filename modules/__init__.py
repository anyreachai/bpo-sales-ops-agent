from modules.classifier import ClassifierModule
from modules.brand_extractor import BrandExtractorModule
from modules.demo_generator import DemoGeneratorModule
from modules.deep_research import DeepResearchModule
from modules.stakeholder_intel import StakeholderIntelModule
from modules.cx_intel import CxIntelModule
from modules.deck_generator import DeckGeneratorModule
from modules.drive_manager import DriveManagerModule
from modules.pipeline_tracker import PipelineTrackerModule
from modules.slack_manager import SlackManagerModule
from modules.email_composer import EmailComposerModule
from orchestrator.registry import register

ALL_MODULES = [
    ClassifierModule(),
    BrandExtractorModule(),
    DemoGeneratorModule(),
    DeepResearchModule(),
    StakeholderIntelModule(),
    CxIntelModule(),
    DeckGeneratorModule(),
    DriveManagerModule(),
    PipelineTrackerModule(),
    SlackManagerModule(),
    EmailComposerModule(),
]

def register_all() -> None:
    for m in ALL_MODULES:
        register(m)
