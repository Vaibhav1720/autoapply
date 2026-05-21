/// App-wide constants.
class AppConstants {
  AppConstants._();

  static const String appName = 'AutoApply';
  static const String appVersion = '0.1.0';

  // Pagination
  static const int defaultPageSize = 20;
  static const int maxAutoApplyPerRequest = 20;

  // File upload
  static const int maxResumeSizeMB = 10;
  static const List<String> allowedResumeTypes = ['pdf'];

  // Match scores
  static const double skillsWeight = 0.35;
  static const double experienceWeight = 0.30;
  static const double locationWeight = 0.20;
  static const double salaryWeight = 0.15;
}
