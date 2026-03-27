#!/usr/bin/env bash
set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Helper functions
print_error() {
  echo -e "${RED}❌ Error: $1${NC}" >&2
}

print_success() {
  echo -e "${GREEN}✅ $1${NC}"
}

print_warn() {
  echo -e "${YELLOW}⚠️  Warning: $1${NC}"
}

print_info() {
  echo -e "${BLUE}ℹ️  $1${NC}"
}

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo -e "${BLUE}📦 Claude Workflow Installation${NC}"
echo "=================================="
echo ""

# 1. Check Python version
print_info "Checking Python..."
if ! command -v python3 &> /dev/null; then
  print_error "Python 3 not found. Please install Python ≥ 3.10"
  exit 1
fi

PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)

if [ "$MAJOR" -lt 3 ] || ([ "$MAJOR" -eq 3 ] && [ "$MINOR" -lt 10 ]); then
  print_error "Python $PYTHON_VERSION found, but ≥ 3.10 required"
  exit 1
fi
print_success "Python $PYTHON_VERSION found"

# 2. Check uv
print_info "Checking uv package manager..."
if ! command -v uv &> /dev/null; then
  print_error "uv not found"
  echo ""
  echo "To install uv, run:"
  echo -e "  ${BLUE}curl -LsSf https://astral.sh/uv/install.sh | sh${NC}"
  exit 1
fi
print_success "uv is installed"

# 3. Check git
print_info "Checking git..."
if ! command -v git &> /dev/null; then
  print_error "git not found. Please install git"
  exit 1
fi
print_success "git is installed"

# 4. Check Claude CLI (warning only)
print_info "Checking Claude CLI..."
if ! command -v claude &> /dev/null; then
  print_warn "Claude CLI not found in PATH"
  echo "  Install it via: https://github.com/anthropics/claude-code"
  echo "  This is required to actually run workflows, but installation can continue."
else
  print_success "Claude CLI is available"
fi

echo ""
print_info "Repository: $SCRIPT_DIR"

# 5. Install with uv
echo ""
print_info "Installing claude-workflow..."
uv tool install --editable "$SCRIPT_DIR" --with pytest --with pytest-cov

# 6. Verify installation
echo ""
print_info "Verifying installation..."

if ! command -v claude-iterative &> /dev/null; then
  print_error "claude-iterative not found after installation"
  echo ""
  echo "This usually means ~/.local/bin is not in your PATH."
  echo "Add this to your shell profile (~/.zshrc, ~/.bashrc, etc):"
  echo ""
  echo -e "  ${BLUE}export PATH=\"\$HOME/.local/bin:\$PATH\"${NC}"
  echo ""
  echo "Then run:"
  echo -e "  ${BLUE}source ~/.zshrc${NC}  # or ~/.bashrc, ~/.bash_profile"
  exit 1
fi

print_success "claude-iterative command is available"

if ! command -v claude-plan-exec &> /dev/null; then
  print_warn "claude-plan-exec not found (this is unexpected)"
else
  print_success "claude-plan-exec command is available"
fi

# 7. Show version/help
echo ""
print_success "Installation complete!"
echo ""
echo "Try it out:"
echo -e "  ${BLUE}claude-iterative --help${NC}"
echo ""
echo "Example usage:"
echo -e "  ${BLUE}claude-iterative -t \"Your task here\" --type feature${NC}"
echo ""
