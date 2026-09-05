class Buildanchor < Formula
  desc "Local-first build truth and change validation for AI coding agents"
  homepage "https://github.com/tensilestream/buildanchor"
  url "https://github.com/tensilestream/buildanchor/archive/refs/tags/v1.1.0.tar.gz"
  version "1.1.0"

  depends_on "python"

  def install
    libexec.install "src/buildanchor"
    (bin / "buildanchor").write <<~EOS
      #!/bin/bash
      export PYTHONPATH="#{libexec}:${PYTHONPATH:-}"
      exec "#{Formula["python"].opt_bin}/python3" -m buildanchor "$@"
    EOS
  end

  test do
    assert_match "usage:", shell_output("#{bin}/buildanchor --help").downcase
  end
end
