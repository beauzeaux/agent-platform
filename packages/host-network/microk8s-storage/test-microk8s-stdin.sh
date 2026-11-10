set -euo pipefail
[[ $- == *e* ]] || exit 99
[[ $# == 4 ]]
[[ "$1" == stdin-probe ]]
[[ "$2" == - ]]
[[ "$3" == 00000000-0000-0000-0000-000000000000 ]]
[[ "$4" == 805306368000 ]]
[[ "$(readlink /proc/self/ns/mnt)" == "$(readlink /proc/1/ns/mnt)" ]]
[[ "$(printf '%s' 'a b "quoted"')" == 'a b "quoted"' ]]
result=$(cat <<EOF
first
second
EOF
)
[[ "$result" == $'first\nsecond' ]]
printf '%s\n' STDIN_ARGUMENTS_OK
