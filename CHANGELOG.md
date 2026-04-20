# Changelog

All notable changes to FPL Predictor will be documented in this file.

## [2.0.0] - 2026-04-20

### Added
- **Model Optimization System** - Admin feature to analyze prediction accuracy and auto-tune weights
  - Calculate MAE, RMSE, and correlation metrics
  - Compare predictions vs actual points for recent gameweeks
  - Suggest weight adjustments based on systematic biases
  - One-click apply to `config.py`
  - Position-specific accuracy analysis
  - Performance grading (A/B/C/D)

- **Enhanced UI/UX**
  - Smooth fade-in animations for page transitions
  - Hover effects on cards (lift + shadow)
  - Gradient text effect on stat values
  - Loading spinner animations
  - Toast notification styles
  - Improved button hover states
  - Responsive mobile design (collapsible sidebar)
  - Better table styling with sticky headers

- **Admin Dashboard Improvements**
  - Model Optimization panel with visual metrics
  - Weight comparison table (current vs suggested)
  - Performance insights and recommendations

### Changed
- Updated README with comprehensive Model Optimization documentation
- Enhanced CSS with modern animations and transitions
- Improved card hover interactions
- Better visual feedback for interactive elements

### Removed
- Cleaned up unused test/debug files:
  - `test_v2.py`
  - `test_v3.py`
  - `debug_gw.py`
  - `debug_player.py`
  - `check_oreilly.py`
  - `time_test.py`

### Security
- Conducted full security audit (see `AUDIT_REPORT.md`)
- Confirmed all credentials loaded from environment variables
- No hardcoded secrets in codebase
- Proper PBKDF2-SHA256 password hashing (600k iterations)

### Documentation
- Added detailed Model Optimization guide in README
- Created comprehensive `AUDIT_REPORT.md`
- Updated API reference with new admin endpoints
- Documented metrics and weight tuning philosophy

---

## [1.0.0] - 2026-03-XX

### Initial Release
- 12-factor Poisson prediction model
- Transfer simulator with FPL-style pitch
- Season-wide chip planning
- AI chat with 12 intent types
- User authentication (Free/Premium/Admin)
- Stripe payment integration
- Real-time injury news aggregation
- Auto-refresh every 2 hours
- Render.com deployment ready
