from pydantic import BaseModel, Field


class AdsRecommendation(BaseModel):
    priority: str = Field(description="high, medium, or low")
    category: str = Field(
        description="One of: bidding, keywords, ad_copy, landing_page, tracking, geo_targeting, budget"
    )
    recommendation: str = Field(description="Specific, actionable recommendation in plain English")
    reasoning: str = Field(
        description="Why this matters, grounded in competitor evidence or Google Ads best practices"
    )


class SourceURL(BaseModel):
    url: str
    title: str


class AuditReport(BaseModel):
    summary: str = Field(description="2-3 sentence executive summary of overall audit findings")
    competitor_comparison: list[str] = Field(
        description="What higher-ranking competitors are doing that the client isn't — specific observations from scraped pages"
    )
    google_ads_recommendations: list[AdsRecommendation] = Field(
        description="Prioritized action items ordered high to low priority"
    )
    geo_recommendations: list[str] = Field(
        description="AI search visibility gaps and suggestions based on observable page signals"
    )
    quick_wins: list[str] = Field(
        description="Exactly 2-3 things the owner can implement today with no technical help"
    )
    open_questions: list[str] = Field(
        description="Things requiring manual verification inside Google Ads, Google Business Profile, or third-party tools"
    )
    sources_consulted: list[SourceURL] = Field(
        description="All URLs scraped (client and competitors) with page titles"
    )
