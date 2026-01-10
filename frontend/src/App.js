import React, { useState } from 'react';
import { Activity, LayoutDashboard, TrendingUp, Wallet } from 'lucide-react';
import './App.css';
import ValueInvesting from './ValueInvesting';
import DualMovingAverage from './DualMovingAverage';
import PortfolioOptimizer from './PortfolioOptimizer';

function App() {
    const [activeTab, setActiveTab] = useState('portfolio-optimizer');

    return (
        <div className="app-container">
            <header className="app-header">
                <div className="brand-section">
                    <Activity size={32} className="text-amber-500" />
                    <h1 className="brand-title">Quant Compass</h1>
                </div>
                <div className="user-controls">
                    {/* Placeholder for future user modules */}
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
                            Portfolio Optimizer
                        </button>
                        <button
                            className={`nav-tab flex items-center gap-2 ${activeTab === 'value-investing' ? 'active' : ''}`}
                            onClick={() => setActiveTab('value-investing')}
                        >
                            <LayoutDashboard size={18} />
                            Value Investing
                        </button>
                        <button
                            className={`nav-tab flex items-center gap-2 ${activeTab === 'dual-moving-average' ? 'active' : ''}`}
                            onClick={() => setActiveTab('dual-moving-average')}
                        >
                            <TrendingUp size={18} />
                            Dual Moving Average
                        </button>
                    </nav>
                </div>

                <div className="tab-content full-width">
                    {activeTab === 'portfolio-optimizer' && <PortfolioOptimizer />}
                    {activeTab === 'value-investing' && <ValueInvesting />}
                    {activeTab === 'dual-moving-average' && <DualMovingAverage />}
                </div>
            </main>
        </div>
    );
}

export default App;
