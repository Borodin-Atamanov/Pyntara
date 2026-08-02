if not contains "$HOME/Downloads/Pyntara/.tmp-uv-bin" $PATH
    # Prepending path in case a system-installed binary needs to be overridden
    set -x PATH "$HOME/Downloads/Pyntara/.tmp-uv-bin" $PATH
end
