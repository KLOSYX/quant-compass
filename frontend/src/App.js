import React, { useState } from 'react';
import { Activity, Wallet, Globe, BarChart3, SlidersHorizontal, ClipboardCheck } from 'lucide-react';
import './App.css';
import PortfolioOptimizer from './PortfolioOptimizer';
import { useLanguage } from './LanguageContext';

function App() {
    const [activeTab, setActiveTab] = useState('portfolio-optimizer');
    const { language, toggleLanguage, t } = useLanguage();

    return (
        <div className="app-container">
            <header className="app-header">
                <div className="brand-section">
                    <div className="brand-mark" aria-hidden="true"><Activity size={21} strokeWidth={2.4} /></div>
                    <div className="brand-copy">
                        <p className="brand-kicker">{t('workbench_kicker')}</p>
                        <h1 className="brand-title">{t('brand')}</h1>
                    </div>
                </div>
                <div className="header-context">
                    <span className="status-indicator"><span aria-hidden="true" />{t('workspace_state_ready')}</span>
                    <button className="language-button" onClick={toggleLanguage} aria-label={t('toggle_language')}>
                        <Globe size={16} />
                        {language === 'zh' ? 'English' : '中文'}
                    </button>
                </div>
            </header>

            <main className="app-main-shell">
                <div className="workbench-intro">
                    <div>
                        <p className="eyebrow">{t('workbench_kicker')}</p>
                        <h2>{t('workbench_title')}</h2>
                        <p className="intro-copy">{t('workbench_subtitle')}</p>
                    </div>
                    <div className="research-stamp" aria-label={t('workspace_state_ready')}>
                        <span className="research-stamp-dot" />
                        <span>{t('workspace_state_ready')}</span>
                    </div>
                </div>

                <nav className="nav-tabs" aria-label={t('workbench_navigation')}>
                    <button
                        className={`nav-tab ${activeTab === 'portfolio-optimizer' ? 'active' : ''}`}
                        onClick={() => setActiveTab('portfolio-optimizer')}
                    >
                        <Wallet size={16} />
                        {t('nav_portfolio')}
                    </button>
                </nav>

                <div className="workflow-rail" aria-label={t('workflow_label')}>
                    <div className="workflow-node active"><span>01</span><strong>{t('workflow_assets')}</strong></div>
                    <div className="workflow-line" />
                    <div className="workflow-node"><span><BarChart3 size={14} /></span><strong>{t('workflow_frontier')}</strong></div>
                    <div className="workflow-line" />
                    <div className="workflow-node"><span><SlidersHorizontal size={14} /></span><strong>{t('workflow_budget')}</strong></div>
                    <div className="workflow-line" />
                    <div className="workflow-node"><span><ClipboardCheck size={14} /></span><strong>{t('workflow_recommendation')}</strong></div>
                </div>

                <div className="tab-content">
                    {activeTab === 'portfolio-optimizer' && <PortfolioOptimizer />}
                </div>
            </main>
        </div>
    );
}

export default App;
