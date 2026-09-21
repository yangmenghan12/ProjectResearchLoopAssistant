export type ResearchTemplateKey =
  | "quick_onboarding"
  | "architecture_understanding"
  | "extension_evaluation"
  | "teaching_breakdown"
  | "delivery_acceptance";

export type ResearchTemplate = {
  key: ResearchTemplateKey;
  label: string;
  description: string;
  goalPrefix: string;
};

export const researchTemplates: ResearchTemplate[] = [
  {
    key: "quick_onboarding",
    label: "快速接手",
    description: "帮新人快速理解项目用途、启动方式、入口文件、核心模块和继续阅读路径。",
    goalPrefix: "快速接手调研：请帮助新人理解项目用途、启动方式、入口文件、核心模块、风险和继续阅读路径。",
  },
  {
    key: "architecture_understanding",
    label: "架构理解",
    description: "面向技术负责人，梳理模块关系、数据流、外部依赖、边界和扩展点。",
    goalPrefix: "架构理解调研：请梳理模块关系、数据流、外部依赖、边界、扩展点和关键风险。",
  },
  {
    key: "extension_evaluation",
    label: "二开评估",
    description: "判断项目是否适合继续开发、改造或集成，关注维护成本和改造风险。",
    goalPrefix: "二开评估调研：请判断项目是否适合继续开发、改造或集成，重点关注维护成本、测试、配置和风险。",
  },
  {
    key: "teaching_breakdown",
    label: "教学拆解",
    description: "把项目拆成学习路径、章节结构、关键代码和难点提示。",
    goalPrefix: "教学拆解调研：请把项目拆成学习路径、章节结构、关键代码、难点和适合讲解的顺序。",
  },
  {
    key: "delivery_acceptance",
    label: "交付验收",
    description: "检查交付项目是否完整、可启动、可维护，并列出待确认问题。",
    goalPrefix: "交付验收调研：请检查项目完整性、启动线索、依赖、文档缺口、维护风险和待确认问题。",
  },
];

export function templateGoal(templateKey: string, userGoal: string): string {
  const template = researchTemplates.find((item) => item.key === templateKey) ?? researchTemplates[0];
  const trimmed = userGoal.trim();
  return trimmed ? `${template.goalPrefix}\n\n用户补充目标：${trimmed}` : template.goalPrefix;
}
