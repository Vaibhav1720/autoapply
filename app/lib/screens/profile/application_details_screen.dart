import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:auto_apply/config/theme.dart';
import 'package:auto_apply/providers/profile_provider.dart';
import 'package:auto_apply/services/api_service.dart';

/// Screen for collecting additional details needed for job applications.
/// These feed the Chrome extension autofill and AI smart-fill.
class ApplicationDetailsScreen extends StatefulWidget {
  const ApplicationDetailsScreen({super.key});
  @override
  State<ApplicationDetailsScreen> createState() => _ApplicationDetailsScreenState();
}

class _ApplicationDetailsScreenState extends State<ApplicationDetailsScreen> {
  final _formKey = GlobalKey<FormState>();
  bool _saving = false;
  String? _message;

  late final TextEditingController _addressCtrl;
  late final TextEditingController _cityCtrl;
  late final TextEditingController _stateCtrl;
  late final TextEditingController _zipCtrl;
  late final TextEditingController _countryCtrl;
  late final TextEditingController _salaryCtrl;
  late final TextEditingController _noticeCtrl;
  late final TextEditingController _coverLetterCtrl;
  late final TextEditingController _firstNameCtrl;
  late final TextEditingController _lastNameCtrl;
  late final TextEditingController _phoneCtrl;
  late final TextEditingController _linkedinCtrl;
  late final TextEditingController _githubCtrl;
  late final TextEditingController _portfolioCtrl;

  String _visaStatus = '';
  String _willingToRelocate = '';
  String _remoteWork = '';
  String _gender = '';
  String _veteranStatus = '';
  String _disability = '';
  String _ethnicity = '';

  @override
  void initState() {
    super.initState();
    final pp = context.read<ProfileProvider>();
    final details = (pp.profile?['applicationDetails'] as Map<String, dynamic>?) ?? {};
    final personal = (pp.profile?['personal'] as Map<String, dynamic>?) ?? {};

    _addressCtrl = TextEditingController(text: details['address'] ?? '');
    _cityCtrl = TextEditingController(text: details['city'] ?? '');
    _stateCtrl = TextEditingController(text: details['state'] ?? '');
    _zipCtrl = TextEditingController(text: details['zip'] ?? '');
    _countryCtrl = TextEditingController(text: details['country'] ?? '');
    _salaryCtrl = TextEditingController(text: details['salaryExpectation'] ?? '');
    _noticeCtrl = TextEditingController(text: details['noticePeriod'] ?? '');
    _coverLetterCtrl = TextEditingController(text: details['coverLetter'] ?? '');
    _firstNameCtrl = TextEditingController(text: personal['firstName'] ?? '');
    _lastNameCtrl = TextEditingController(text: personal['lastName'] ?? '');
    _phoneCtrl = TextEditingController(text: personal['phone'] ?? '');
    _linkedinCtrl = TextEditingController(text: pp.profile?['linkedinUrl'] ?? '');
    _githubCtrl = TextEditingController(text: personal['githubUrl'] ?? '');
    _portfolioCtrl = TextEditingController(text: personal['portfolioUrl'] ?? '');

    _visaStatus = details['visaStatus'] ?? '';
    _willingToRelocate = details['willingToRelocate'] ?? '';
    _remoteWork = details['remoteWork'] ?? '';
    _gender = details['gender'] ?? '';
    _veteranStatus = details['veteranStatus'] ?? '';
    _disability = details['disability'] ?? '';
    _ethnicity = details['ethnicity'] ?? '';
  }

  @override
  void dispose() {
    _addressCtrl.dispose();
    _cityCtrl.dispose();
    _stateCtrl.dispose();
    _zipCtrl.dispose();
    _countryCtrl.dispose();
    _salaryCtrl.dispose();
    _noticeCtrl.dispose();
    _coverLetterCtrl.dispose();
    _firstNameCtrl.dispose();
    _lastNameCtrl.dispose();
    _phoneCtrl.dispose();
    _linkedinCtrl.dispose();
    _githubCtrl.dispose();
    _portfolioCtrl.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    if (!_formKey.currentState!.validate()) return;
    setState(() { _saving = true; _message = null; });

    try {
      final api = context.read<ApiService>();
      await api.put('/api/v1/profile/application-details', data: {
        'firstName': _firstNameCtrl.text.trim(),
        'lastName': _lastNameCtrl.text.trim(),
        'phone': _phoneCtrl.text.trim(),
        'linkedinUrl': _linkedinCtrl.text.trim(),
        'githubUrl': _githubCtrl.text.trim(),
        'portfolioUrl': _portfolioCtrl.text.trim(),
        'address': _addressCtrl.text.trim(),
        'city': _cityCtrl.text.trim(),
        'state': _stateCtrl.text.trim(),
        'zip': _zipCtrl.text.trim(),
        'country': _countryCtrl.text.trim(),
        'salaryExpectation': _salaryCtrl.text.trim(),
        'noticePeriod': _noticeCtrl.text.trim(),
        'coverLetter': _coverLetterCtrl.text.trim(),
        'visaStatus': _visaStatus,
        'willingToRelocate': _willingToRelocate,
        'remoteWork': _remoteWork,
        'gender': _gender,
        'veteranStatus': _veteranStatus,
        'disability': _disability,
        'ethnicity': _ethnicity,
      });
      // Reload profile to sync
      if (mounted) {
        context.read<ProfileProvider>().loadProfile();
        setState(() { _message = 'Saved! These details will be used for autofill.'; });
      }
    } catch (e) {
      setState(() { _message = 'Error: $e'; });
    } finally {
      if (mounted) setState(() { _saving = false; });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Application Details')),
      body: Form(
        key: _formKey,
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            const Text(
              'Fill in details commonly asked in job applications. '
              'These power the Chrome extension autofill.',
              style: TextStyle(color: AppTheme.textSecondary, fontSize: 13),
            ),
            const SizedBox(height: 16),

            // Personal / Contact — these go in the resume parser by default but
            // user can edit if missing or wrong.
            _SectionHeader('Personal & Contact'),
            Row(children: [
              Expanded(child: _buildField('First name', _firstNameCtrl)),
              const SizedBox(width: 12),
              Expanded(child: _buildField('Last name', _lastNameCtrl)),
            ]),
            _buildField('Phone number', _phoneCtrl, hint: 'e.g. +91 98765 43210'),
            _buildField('LinkedIn URL (optional)', _linkedinCtrl, hint: 'https://linkedin.com/in/...'),
            _buildField('GitHub URL (optional)', _githubCtrl, hint: 'https://github.com/...'),
            _buildField('Portfolio / website (optional)', _portfolioCtrl, hint: 'https://...'),
            const SizedBox(height: 20),

            // Address
            _SectionHeader('Address'),
            _buildField('Street Address', _addressCtrl),
            Row(children: [
              Expanded(child: _buildField('City', _cityCtrl)),
              const SizedBox(width: 12),
              Expanded(child: _buildField('State', _stateCtrl)),
            ]),
            Row(children: [
              Expanded(child: _buildField('Zip Code', _zipCtrl)),
              const SizedBox(width: 12),
              Expanded(child: _buildField('Country', _countryCtrl)),
            ]),
            const SizedBox(height: 20),

            // Work Authorization
            _SectionHeader('Work Authorization'),
            _buildDropdown('Visa / Work Authorization', _visaStatus, [
              '', 'US Citizen', 'Green Card', 'H1B', 'H4 EAD', 'OPT', 'CPT',
              'L1', 'TN', 'O1', 'Need Sponsorship', 'Other',
            ], (v) => setState(() => _visaStatus = v)),
            _buildDropdown('Willing to Relocate', _willingToRelocate, [
              '', 'Yes', 'No', 'Open to discussion',
            ], (v) => setState(() => _willingToRelocate = v)),
            _buildDropdown('Open to fully remote work', _remoteWork, [
              '', 'Yes', 'No', 'Hybrid only',
            ], (v) => setState(() => _remoteWork = v)),
            const SizedBox(height: 20),

            // Compensation
            _SectionHeader('Compensation & Availability'),
            _buildField('Salary Expectation (optional)', _salaryCtrl, hint: 'e.g. \$120,000 or 25 LPA'),
            _buildField('Notice Period (optional)', _noticeCtrl, hint: 'e.g. 2 weeks, Immediately'),
            const SizedBox(height: 20),

            // EEO (optional)
            _SectionHeader('Equal Opportunity (Optional)'),
            const Text(
              'These are optional and only used if job applications ask.',
              style: TextStyle(color: AppTheme.textSecondary, fontSize: 12),
            ),
            const SizedBox(height: 8),
            _buildDropdown('Gender', _gender, [
              '', 'Male', 'Female', 'Non-binary', 'Prefer not to say',
            ], (v) => setState(() => _gender = v)),
            _buildDropdown('Veteran Status', _veteranStatus, [
              '', 'Not a veteran', 'Veteran', 'Prefer not to say',
            ], (v) => setState(() => _veteranStatus = v)),
            _buildDropdown('Disability', _disability, [
              '', 'No', 'Yes', 'Prefer not to say',
            ], (v) => setState(() => _disability = v)),
            _buildDropdown('Ethnicity', _ethnicity, [
              '', 'Asian', 'Black or African American', 'Hispanic or Latino',
              'White', 'Native American', 'Pacific Islander', 'Two or more races',
              'Prefer not to say',
            ], (v) => setState(() => _ethnicity = v)),
            const SizedBox(height: 20),

            // Cover Letter
            _SectionHeader('Default Cover Letter'),
            TextFormField(
              controller: _coverLetterCtrl,
              maxLines: 6,
              decoration: const InputDecoration(
                hintText: 'Write a default cover letter / motivation statement...',
                border: OutlineInputBorder(),
              ),
            ),
            const SizedBox(height: 24),

            if (_message != null)
              Padding(
                padding: const EdgeInsets.only(bottom: 12),
                child: Text(_message!,
                  style: TextStyle(
                    color: _message!.startsWith('Error') ? Colors.red : AppTheme.success,
                    fontSize: 13,
                  )),
              ),

            FilledButton.icon(
              onPressed: _saving ? null : _save,
              icon: _saving
                ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                : const Icon(Icons.save),
              label: Text(_saving ? 'Saving...' : 'Save Application Details'),
              style: FilledButton.styleFrom(
                padding: const EdgeInsets.symmetric(vertical: 14),
                backgroundColor: AppTheme.primary,
              ),
            ),
            const SizedBox(height: 32),
          ],
        ),
      ),
    );
  }

  Widget _buildField(String label, TextEditingController ctrl, {String? hint}) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: TextFormField(
        controller: ctrl,
        decoration: InputDecoration(
          labelText: label,
          hintText: hint,
          border: const OutlineInputBorder(),
          isDense: true,
        ),
      ),
    );
  }

  Widget _buildDropdown(String label, String value, List<String> options, ValueChanged<String> onChanged) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: DropdownButtonFormField<String>(
        value: options.contains(value) ? value : '',
        decoration: InputDecoration(labelText: label, border: const OutlineInputBorder(), isDense: true),
        items: options.map((o) => DropdownMenuItem(value: o, child: Text(o.isEmpty ? '— Select —' : o))).toList(),
        onChanged: (v) => onChanged(v ?? ''),
      ),
    );
  }
}

class _SectionHeader extends StatelessWidget {
  final String title;
  const _SectionHeader(this.title);
  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Text(title, style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600)),
    );
  }
}
