# Homebrew formula for docus.
#
# To use this as a personal tap:
#
#   1. Create a repo named homebrew-tap under your GitHub account.
#   2. Copy this file to Formula/docus.rb in that repo.
#   3. Fill in the sha256 after creating the v0.1.0 GitHub release:
#
#        curl -sL https://github.com/jvrsantacruz/docus/archive/refs/tags/v0.1.0.tar.gz \
#          | shasum -a 256
#
#   4. Install with:
#
#        brew tap jvrsantacruz/tap
#        brew install docus

class Docus < Formula
  desc "CLI Documentation Extractor — generates Markdown from any CLI tool's --help output"
  homepage "https://github.com/jvrsantacruz/docus"
  url "https://github.com/jvrsantacruz/docus/archive/refs/tags/v0.1.0.tar.gz"
  sha256 "REPLACE_WITH_SHA256_OF_v0.1.0_TARBALL"
  license "GPL-3.0-or-later"
  head "https://github.com/jvrsantacruz/docus.git", branch: "main"

  depends_on "python@3.11"

  def install
    bin.install "docus.py" => "docus"
  end

  test do
    assert_match "usage: docus", shell_output("#{bin}/docus --help")
  end
end
