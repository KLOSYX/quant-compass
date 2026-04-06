import { render, screen } from '@testing-library/react';
import App from './App';
import { LanguageProvider } from './LanguageContext';

test('renders portfolio optimizer tab', () => {
  render(
    <LanguageProvider>
      <App />
    </LanguageProvider>
  );
  const tabElement = screen.getByRole('button', { name: /Portfolio Optimizer/ });
  expect(tabElement).toBeInTheDocument();
});
