{ lib, user, ... }:

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
      {
        name = "automic-vault/isotopes";
        trusted = true;
      }
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
      "automic-vault/isotopes/automic-vault"
    ];
  };

  # The cask contains the vendor-signed CLI, but Homebrew installs only the app.
  # Run the vendor's supported installer after the Homebrew activation. Never
  # replace an existing CLI unless it already matches the app byte-for-byte.
  system.activationScripts.postActivation.text = lib.mkAfter ''
    atomic_vault_app="/Applications/Automic Vault.app"
    atomic_vault_cli="/usr/local/bin/av"
    atomic_vault_bundled_cli="$atomic_vault_app/Contents/MacOS/av"
    atomic_vault_installer="$atomic_vault_app/Contents/Resources/install-av-cli.command"

    if [[ ! -x "$atomic_vault_bundled_cli" || ! -x "$atomic_vault_installer" ]]; then
      echo "Atomic Vault CLI installer is missing from the declared cask" >&2
      exit 1
    fi
    if ! /usr/bin/codesign --verify --deep --strict "$atomic_vault_app" >/dev/null 2>&1; then
      echo "Atomic Vault app signature verification failed" >&2
      exit 1
    fi

    # The cask's signed app and pinned Homebrew download are the trust boundary
    # for the CLI. Refuse symlinks, directories, or unrelated existing binaries.
    if [[ -e "$atomic_vault_cli" || -L "$atomic_vault_cli" ]]; then
      if [[ ! -f "$atomic_vault_cli" || -L "$atomic_vault_cli" ]] ||
         ! /usr/bin/cmp -s "$atomic_vault_bundled_cli" "$atomic_vault_cli" ||
         [[ "$(/usr/bin/stat -f '%Su:%Sg:%Lp' "$atomic_vault_cli")" != "root:wheel:755" ]]; then
        echo "Refusing to replace existing $atomic_vault_cli; it does not match the Atomic Vault app CLI or permissions" >&2
        echo "Review it and run the vendor installer from $atomic_vault_app/Contents/Resources/install-av-cli.command" >&2
        exit 1
      fi
    else
      echo "Installing Atomic Vault CLI with the vendor-provided installer..." >&2
      if ! /bin/sh "$atomic_vault_installer"; then
        # The installer also notifies the app with `open`; activation is already
        # root, so a GUI notification may fail even when the CLI was installed.
        if [[ ! -f "$atomic_vault_cli" ]] ||
           ! /usr/bin/cmp -s "$atomic_vault_bundled_cli" "$atomic_vault_cli"; then
          echo "Atomic Vault CLI installation failed" >&2
          exit 1
        fi
      fi
    fi

    if ! /usr/bin/cmp -s "$atomic_vault_bundled_cli" "$atomic_vault_cli"; then
      echo "Atomic Vault CLI does not match the declared app; refusing to continue" >&2
      exit 1
    fi
  '';
}
