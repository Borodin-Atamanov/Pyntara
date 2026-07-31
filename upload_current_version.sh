#!/usr/bin/bash
sleep_time='0.42'
##set -x
vault/encrypt_all_vaults.sh
source index.sh fun
sleep "$sleep_time"
time (
 git diff | head --lines=17
 sleep "$sleep_time"
 echo git pull --verbose
 git whatchanged | head --lines=13
 #find and delete old script_version set from index.sh
 #cat index.sh | grep -v '^script_version' | grep -v '^export script_version' | cat > index_w_script_version.sh
 #script_version="$(random_str 5)$(random_str 1)-$(git describe --always --tags)-$(date "+%F-%H-%M-%S")"
 #add script_version to index.sh

 #change version in file
 load_var_from_file "index.sh" index_sh_code
 git_commits_counter="$(git rev-list --all --count)"
 git_commits_counter=$(( git_commits_counter + 0 ))
 script_version="$(random_str 5)$(random_str 1)-${git_commits_counter}-$(date "+%y%m%d%H%M")"
 script_version_code_preffix='declare -g -x script_version='
 script_version_code="${script_version_code_preffix}'${script_version}'; "
 replace_line_by_string index_sh_code "${script_version_code_preffix}" "${script_version_code}" '#'
 save_var_to_file "index.sh" index_sh_code
 echo " ● ${script_version} ● "

 #echo -n "export script_version='${script_version}'; " >> "index_w_script_version.sh"
 #echo -n 'echo "${script_version}=script_version"; ' >> "index_w_script_version.sh"
 # echo '"'; >> "index_w_script_version.sh"
 #echo -e "\n"; >> "index_w_script_version.sh"
 #cp --update --verbose "index_w_script_version.sh" "index.sh"
 #rm -v "index_w_script_version.sh"
 #
 sleep "$sleep_time"
 git add --verbose --all
 sleep "$sleep_time"
 git commit --allow-empty-message --message="$script_version" --verbose
 git push --verbose
 echo " ● ${script_version} ● "
 #check all syntax without running
 find . -name '*.sh' -print0 | xargs -0 -P"$(nproc)" -I {} bash -n "{}"
)
