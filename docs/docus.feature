Feature: CLI documentation extraction

  docus generates a single Markdown document from a CLI tool by recursively
  invoking --help on every discovered subcommand.

  Scenario: Document a CLI tool to a file
    Given a CLI tool with subcommands
    When the user runs "docus <tool>"
    Then a Markdown file named "<tool>.md" is created
    And it contains a heading with the tool name
    And it contains an Index table linking every discovered command

  Scenario: Print documentation to stdout
    Given a CLI tool with subcommands
    When the user runs "docus --stdout <tool>"
    Then the Markdown is written to stdout
    And no file is created

  Scenario: Start the tree at a subcommand entry point
    Given a CLI tool "mycli" with a subcommand "config"
    When the user runs "docus mycli config"
    Then the output is rooted at "mycli config"
    And parent commands are not included

  Scenario: Limit recursion depth
    Given a CLI tool with commands nested 3 levels deep
    When the user runs "docus --max-depth 1 <tool>"
    Then depth-1 subcommands appear in the output
    And depth-2 subcommands do not appear

  Scenario: Filter output by topic
    Given a CLI tool whose subcommands cover unrelated topics
    When the user runs "docus --like <term> <tool>"
    Then only sections relevant to <term> appear in the output
    And the output is shorter than without the filter

  Scenario: Deduplicate repeated content
    Given a CLI tool where multiple subcommands share an identical long section
    When the user runs "docus <tool>"
    Then the shared content appears in full only on its first occurrence
    And subsequent occurrences are replaced with a reference marker
