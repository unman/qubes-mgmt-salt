#!yamlscript
# vim: set syntax=yaml ts=2 sw=2 sts=2 et :
#
# Initially, everything will break without a public key being set
#
# Run this to set the key before highstate:
# salt-call --local state.sls salt.gnupg

gnupg:
  pkg.installed:
    - names:
      - python-gnupg
    - order: 1
    #- require:
    #  - pkg: salt

  #gpg.import_key:
  #  # source: salt://keys/nrgaway-qubes-signing-key.asc
  #  # source: salt://gnupg/keys/nrgaway-qubes-signing-key.asc
  #  # source: {{ pillar['gnupg-key'] }}
  #  # source: $pillar('key')
  #  - contents_pillar: $pillar('gnupg-key')
  #  - order: 1
  #  # gpg-keydir: /etc/salt/gpgkeys

  #file.directory:
  #  - name: /etc/salt/gpgkeys
  #  - source: salt://salt/files/gpgkeys
  #  - makedirs: True
  #  - user: root
  #  - group: root
  #  - dir_mode: 700
  #  - file_mode: 600
  #  - recurse:
  #    - user
  #    - group
  #    - mode
  #  # require:
  #  # - pkg: gnupg

# ==============================================================================
# OLD State Code
# ==============================================================================

# gpgkeys
#/etc/salt/gpgkeys/pubring.gpg:
#  file.managed:
#    - source: salt://salt/files/gpgkeys/pubring.gpg
#    - replace: False
#    - makedirs: True
#    - user: root
#    - group: root
#    - mode: 600
#    - require:
#      - pkg: gnupg

#/etc/salt/gpgkeys/trustdb.gpg:
#  file.managed:
#    - source: salt://salt/files/gpgkeys/trustdb.gpg
#    - replace: False
#    - makedirs: True
#    - user: root
#    - group: root
#    - mode: 600
#    - require:
#      - pkg: gnupg
