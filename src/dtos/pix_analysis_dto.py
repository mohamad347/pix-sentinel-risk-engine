from models import PixAnalysis


class PixAnalysisDTO:
    @staticmethod
    def obj_to_dict(analysis: PixAnalysis) -> dict:
        return {
            "key": str(analysis.analysis_key),
            "status": analysis.status,
            "metadata": analysis.decision_metadata,
        }
