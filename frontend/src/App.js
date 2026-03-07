import React, { useState } from 'react';
import { Activity, Wallet, Globe } from 'lucide-react';
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
                    <Activity size={32} className="text-amber-500" />
                    <h1 className="brand-title">{t('brand')}</h1>
                </div>
                <div className="user-controls">
                    <button className="text-link-btn" onClick={toggleLanguage}>
                        <Globe size={18} />
                        {language === 'zh' ? 'English' : '中文'}
                    </button>
                </div>
            </header>

            <main className="app-main block p-0">
                <div className="full-width px-8 pt-6">
                    <nav className="nav-tabs">
                        <button
                            className={`nav-tab flex items-center gap-2 ${activeTab === 'portfolio-optimizer' ? 'active' : ''}`}
                            onClick={() => setActiveTab('portfolio-optimizer')}
                        >
                            <Wallet size={18} />
                            {t('nav_portfolio')}
                        </button>

                    </nav>
                </div>

                <div className="tab-content full-width">
                    {activeTab === 'portfolio-optimizer' && <PortfolioOptimizer />}

                </div>
            </main>
        </div>
    );
}

export default App;
