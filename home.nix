{ config, pkgs, user, nixpkgs-unstable, ... }:

let
  dotfiles = "${config.home.homeDirectory}/.dotfiles";
  # 26.05 has no grok-build at all; the flake's unstable input carries it.
  # The separate instance needs configuration.nix's nixpkgs policy because the
  # package is unfree (`unfreeRedistributable`).
  unstable = import nixpkgs-unstable {
    system = pkgs.stdenv.hostPlatform.system;
    inherit (pkgs) config;
  };
  # Pi is installed globally by npm, but Node 24.16.0 from the primary input
  # has a Darwin worker-thread fd-tracking regression. Keep the normal nodejs
  # package for other consumers and run only Pi with the corrected unstable Node.
  piLauncher = pkgs.writeShellScriptBin "pi" (
    builtins.replaceStrings
      [ "@UNSTABLE_NODE_BIN@" ]
      [ "${unstable.nodejs}/bin" ]
      (builtins.readFile ./home/pi-launcher.sh)
  );
  # OpenCode is the coding agent Firstmate's adapter drives through the
  # interactive `opencode --model` flag. OpenCode 2.0 removed that flag, so the
  # compatible line is 1.x: pin the exact 1.18.32 release the adapter was
  # verified against, and wrap it with OpenCode's auto-update gate so the pin
  # cannot silently move. The vendor ships a self-contained binary per platform
  # (its npm package is a thin installer around it), so fetch the official
  # Darwin arm64 release instead of building through the bun toolchain.
  opencode = pkgs.stdenv.mkDerivation {
    pname = "opencode";
    version = "1.18.32";

    src = pkgs.fetchurl {
      url = "https://github.com/anomalyco/opencode/releases/download/v1.18.32/opencode-darwin-arm64.zip";
      hash = "sha256-+mQ/k0AcE1CNjVE3gOVM6cwBID1QERS+m4jWJAi4EB8=";
    };

    nativeBuildInputs = [ pkgs.unzip pkgs.makeWrapper ];

    dontUnpack = true;
    dontStrip = true;

    installPhase = ''
      runHook preInstall
      mkdir -p "$out/bin"
      unzip -q "$src" -d "$out/bin"
      chmod +x "$out/bin/opencode"
      # /usr/sbin carries `sysctl`, which OpenCode reads for machine metadata on
      # macOS and which a minimal inherited worker PATH can omit.
      wrapProgram "$out/bin/opencode" \
        --prefix PATH : /usr/sbin \
        --set OPENCODE_DISABLE_AUTOUPDATE true
      runHook postInstall
    '';

    meta = with pkgs.lib; {
      description = "AI coding agent built for the terminal (pinned 1.x for Firstmate's adapter)";
      homepage = "https://opencode.ai";
      license = licenses.mit;
      platforms = [ "aarch64-darwin" ];
      mainProgram = "opencode";
    };
  };
in

{
  imports = [ ./home-manager/common.nix ];

  home.username = user;
  home.homeDirectory = "/Users/${user}";
  home.stateVersion = "24.11";
  home.packages = with pkgs; [
    # cli i use constantly
    ripgrep   # fast search
    fd        # fast find
    fzf       # fuzzy finder
    jq        # json on the command line
    gh        # GitHub CLI
    lazygit
    neovim
    nodejs    # default Node runtime for non-Pi consumers
    piLauncher # run the npm-installed `pi` with corrected Node 24 (see README)
    opencode   # pinned OpenCode 1.18.32 for Firstmate's adapter (see README)
    # the font everything renders in
    nerd-fonts.hack
    # Grok CLI (xAI's coding agent), from the nixpkgs-unstable input in flake.nix
    unstable.grok-build
  ];
  fonts.fontconfig.enable = true;
  home.sessionVariables.EDITOR = "nvim";
  # Homebrew's /etc/zshrc prepends its bin directories after zshenv has
  # loaded Home Manager's session variables. Keep the profile first so the
  # corrected Pi launcher wins in fresh login shells and inherited workers.
  home.sessionPath = [ "${config.home.profileDirectory}/bin" ];

  programs.zsh = {
    enable = true;
    autosuggestion.enable = true;      # ghost text from history
    syntaxHighlighting.enable = true;  # commands turn green when valid
    initContent = ''
      bindkey '^f' autosuggest-accept
      # macOS /etc/zshrc runs `brew shellenv` after zshenv, so reassert the
      # Home Manager profile after Homebrew has prepended its directories.
      path=("${config.home.profileDirectory}/bin" $path)
    '';
    shellAliases = {
      ".." = "cd ..";
      add = "git add .";
      push = "git push";
      pull = "git pull";
      m = "git switch main";
      cc = "claude --dangerously-skip-permissions";
      co = "codex --sandbox workspace-write --ask-for-approval never";
    };
  };

  programs.starship = {
    enable = true;
    settings = {
      add_newline = false;
      format = "$directory$git_branch$git_status$cmd_duration$line_break$character";
      character = {
        success_symbol = "[❯](purple)";
        error_symbol = "[❯](red)";
      };
      cmd_duration.format = "[$duration]($style) ";
    };
  };

  # Edit-in-place: the real file stays in my repo, ~/.config just points at it.
  home.file.".config/wezterm".source =
    config.lib.file.mkOutOfStoreSymlink "${dotfiles}/home/.config/wezterm";
  home.file.".config/nvim".source =
    config.lib.file.mkOutOfStoreSymlink "${dotfiles}/home/.config/nvim";
  home.file.".config/herdr".source =
    config.lib.file.mkOutOfStoreSymlink "${dotfiles}/home/.config/herdr";
  home.file.".claude/settings.json".source =
    config.lib.file.mkOutOfStoreSymlink "${dotfiles}/home/.claude/settings.json";

  # Keep Pi's credential and runtime state local by linking only authored files and directories.
  home.file.".pi/agent/themes".source =
    config.lib.file.mkOutOfStoreSymlink "${dotfiles}/home/.pi/agent/themes";
  home.file.".pi/agent/extensions".source =
    config.lib.file.mkOutOfStoreSymlink "${dotfiles}/home/.pi/agent/extensions";
  home.file.".pi/agent/models.json".source =
    config.lib.file.mkOutOfStoreSymlink "${dotfiles}/home/.pi/agent/models.json";
  home.file.".pi/agent/settings.json".source =
    config.lib.file.mkOutOfStoreSymlink "${dotfiles}/home/.pi/agent/settings.json";

  home.file.".claude/CLAUDE.md".source =
    config.lib.file.mkOutOfStoreSymlink "${dotfiles}/home/AGENTS.md";
  home.file.".codex/AGENTS.md".source =
    config.lib.file.mkOutOfStoreSymlink "${dotfiles}/home/AGENTS.md";
  home.file.".config/opencode/AGENTS.md".source =
    config.lib.file.mkOutOfStoreSymlink "${dotfiles}/home/AGENTS.md";
}
