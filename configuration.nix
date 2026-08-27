{ user, ... }:

{
  # Determinate already manages the Nix daemon, so nix-darwin shouldn't.
  nix.enable = false;

  nixpkgs.config.allowUnfree = true;
  nixpkgs.hostPlatform = "aarch64-darwin"; # use x86_64-darwin for Intel CPU

  system.primaryUser = user;
  users.users.${user} = {
    home = "/Users/${user}";
  };
  system.stateVersion = 6;
  system.defaults = {
    NSGlobalDomain = {
      AppleInterfaceStyle = "Dark";
      KeyRepeat = 2;          # fast key repeat
      InitialKeyRepeat = 15;  # short delay before repeat
      _HIHideMenuBar = true;  # auto-hide the menu bar
      AppleShowAllExtensions = true;
    };
    dock.autohide = true;
    finder.FXPreferredViewStyle = "Nlsv";  # list view by default
    finder.CreateDesktop = false;          # clean desktop
    trackpad.Clicking = true;              # tap to click
  };
  nix-homebrew = {
    enable = true;
    inherit user;
  };
  homebrew = {
    enable = true;
    onActivation.cleanup = "zap";  # remove anything not listed here
    onActivation.autoUpdate = true;
    onActivation.extraFlags = [ "--force" ];
    # Homebrew 6.0.0 requires interactive `brew trust` for non-official taps
    # (like kunchenguid/tap below) before it will load their casks/formulae -
    # including inside the internal `brew cleanup` subprocess that `--force-cleanup`
    # spawns, which isn't reachable by an interactive trust grant at all under
    # sudo-driven activation. Taps only reach the Brewfile via a reviewed commit
    # to this file, so Homebrew's own interactive re-confirmation on top of that
    # is redundant here.
    onActivation.extraEnv = {
      HOMEBREW_NO_REQUIRE_TAP_TRUST = "1";
    };
    taps = [
      "kunchenguid/tap"
    ];
    brews = [
      "herdr"
    ];
    casks = [
      "wezterm"
      "claude-code"
      "codex"
      "opensuperwhisper"
      "baby-menu"
    ];
  };
}
