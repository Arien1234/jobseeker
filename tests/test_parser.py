"""Parser tests against the DOM structure observed on 2026-11-21."""

from core.job_parser import (
    canonical_job_url,
    company_size_from_html,
    company_size_text,
    hr_activity_from_html,
    hr_activity_text,
    load_selectors,
    parse_card_html,
    parse_detail_html,
    parse_panel_html,
)

CARD_HTML = """
<div class="job-card job-card--active">
  <div class="job-card__title-row">
    <span class="vue-clamp__text" aria-label="Python开发工程师">Python开发工程师</span>
    <span class="job-card__salary">10000-12000元</span>
  </div>
  <img class="job-card__company-logo" src="logo.png" alt="示例科技公司">
  <a class="job-card__company-name" title="示例科技公司" href="https://www.zhaopin.com/companydetail/CZ1.htm">示例科技公司</a>
  <div class="job-card__location"><span title="广州 天河">广州 天河</span></div>
</div>
"""

PANEL_HTML = """
<div class="job-detail-modules">
  <div class="job-detail-summary__title-text">Python开发工程师</div>
  <span class="job-detail-summary__salary">10000-12000元</span>
  <span class="job-detail-summary__tag">深圳·南山区</span>
  <span class="job-detail-summary__tag">1-3年</span>
  <span class="job-detail-summary__tag">本科</span>
  <div class="job-detail-card">
    <div class="job-detail-card__title">职位描述</div>
    <div class="job-detail-card__body">负责模型推理服务</div>
  </div>
  <div class="job-detail-publisher">
    <div class="job-detail-publisher__name-row">
      <span>陈女士</span>
      <span class="job-detail-publisher__online">在线</span>
      <span class="job-detail-publisher__activity-tag">刚刚活跃</span>
      <span class="job-detail-publisher__activity-tag">总回复 30+</span>
    </div>
  </div>
  <a class="job-detail-view-more" href="https://www.zhaopin.com/jobdetail/CCL1480117890J40910946705.htm?srccode=408901">查看更多职位信息</a>
</div>
"""

DETAIL_HTML = """
<div class="summary-planes">
  <h1 class="summary-planes__title">Python开发工程师</h1>
  <span class="summary-planes__salary">10000-12000元</span>
  <ul class="summary-planes__info">
    <li>广州 天河</li>
    <li>经验不限</li>
    <li>本科</li>
    <li class="summary-planes__info-company">客户公司：某客户</li>
  </ul>
</div>
<div class="company-summary__name">示例科技公司</div>
<div class="describtion-card">
  <span class="describtion-card__skills-item">Python</span>
  <div class="describtion-card__detail-content">负责后端接口开发</div>
</div>
"""


def test_selectors_match_observed_classes():
    classes = load_selectors()["classes"]
    assert classes["card"] == "job-card"
    assert classes["detail_description"] == "describtion-card__detail-content"
    assert classes["detail_company"] == "company-summary__name"
    assert load_selectors()["pagination"]["mode"] == "document_scroll"


def test_parse_card_from_observed_structure():
    job = parse_card_html(CARD_HTML)
    assert job["title"] == "Python开发工程师"
    assert job["salary"] == "10000-12000元"
    assert job["company"] == "示例科技公司"
    assert job["location"] == "广州 天河"


def test_parse_panel_and_canonical_detail_url():
    panel = parse_panel_html(PANEL_HTML)
    assert panel["location"] == "深圳·南山区"
    assert panel["experience"] == "1-3年"
    assert panel["education"] == "本科"
    assert panel["description"] == "负责模型推理服务"
    assert panel["hr_activity"] == "刚刚活跃 · 总回复 30+"
    assert panel["url"] == "https://www.zhaopin.com/jobdetail/CCL1480117890J40910946705.htm"
    assert canonical_job_url(panel["url"] + "?data_identity=abc") == panel["url"]


def test_parse_detail_uses_employer_not_client():
    detail = parse_detail_html(DETAIL_HTML)
    assert detail["company"] == "示例科技公司"
    assert "某客户" not in detail["company"]
    assert detail["education"] == "本科"
    assert detail["experience"] == "经验不限"
    assert detail["location"] == "广州 天河"
    assert "Python" in detail["description"]
    assert "负责后端接口开发" in detail["description"]


def test_panel_keeps_skill_tags_and_requirements():
    html = """
    <div class="job-detail-modules">
      <div class="job-detail-summary__title-text">后端开发工程师</div>
      <span class="job-detail-summary__salary">9000-15000元</span>
      <div class="job-detail-card">
        <div class="job-detail-card__title">职位描述</div>
        <span>Python</span>
        <span>Java</span>
        <div class="job-detail-card__body">岗位职责:
1.负责后端开发工作
任职要求：
1.熟练使用 Java、Python</div>
      </div>
    </div>
    """
    panel = parse_panel_html(html)
    assert "Python" in panel["description"]
    assert "Java" in panel["description"]
    assert "岗位职责" in panel["description"]
    assert "任职要求" in panel["description"]


def test_company_size_ignores_headcount_and_keeps_the_largest_range():
    text = "招1人 客户公司人数 500-999人 公司信息 B轮 · 500-999人 · 人力资源 20-99人"
    assert company_size_text(text) == "500-999人"
    assert company_size_text("10000人以上") == "10000人以上"


def test_company_size_comes_from_the_job_company_block():
    html = """
    <div class="join-company-info__item">
      <span class="join-company-info__label">客户公司人数</span>
      <span class="join-company-info__value">500-999人</span>
    </div>
    <li class="company-summary__item">
      <span class="company-summary__icon company-summary__icon--scale"></span>
      <span class="company-summary__text">20-99人</span>
    </li>
    """
    assert company_size_from_html(html) == "500-999人"
    direct = """
    <li class="company-summary__item"><span class="company-summary__text">未融资</span></li>
    <li class="company-summary__item">
      <span class="company-summary__icon company-summary__icon--scale"></span>
      <span class="company-summary__text">10000人以上</span>
    </li>
    <div class="similar">20-99人</div>
    """
    assert company_size_from_html(direct) == "10000人以上"


def test_hr_activity_prefers_the_strongest_label():
    assert hr_activity_text("在线", "当前在线", "总回复 30+") == "当前在线 · 总回复 30+"
    assert hr_activity_text("本周活跃") == "本周活跃"
    html = """
    <div class="publisher-seo__name-row">
      <span class="publisher-seo__online">在线</span>
      <span class="publisher-seo__activity-tag">今日活跃</span>
      <span class="publisher-seo__activity-tag">总回复 10+</span>
    </div>
    """
    assert hr_activity_from_html(html) == "今日活跃 · 总回复 10+"
