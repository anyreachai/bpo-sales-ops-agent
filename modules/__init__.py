from modules.classifier import ClassifierModule
from modules.brand_extractor import BrandExtractorModule
from modules.demo_generator import DemoGeneratorModule
from modules.deep_research import DeepResearchModule
from modules.openai_research import OpenAIResearchModule
from modules.stakeholder_intel import StakeholderIntelModule
from modules.cx_intel import CxIntelModule
from modules.deck_generator import DeckGeneratorModule
from modules.drive_manager import DriveManagerModule
from modules.pipeline_tracker import PipelineTrackerModule
from modules.slack_manager import SlackManagerModule
from modules.email_composer import EmailComposerModule
from modules.attio_sync import AttioSyncModule
from orchestrator.registry import register

ALL_MODULES = [
    ClassifierModule(),
    BrandExtractorModule(),
    DemoGeneratorModule(),
    DeepResearchModule(),
    OpenAIResearchModule(),
    StakeholderIntelModule(),
    CxIntelModule(),
    DeckGeneratorModule(),
    DriveManagerModule(),
    PipelineTrackerModule(),
    SlackManagerModule(),
    EmailComposerModule(),
    AttioSyncModule(),
]

def register_all() -> None:
    for m in ALL_MODULES:
        register(m)
