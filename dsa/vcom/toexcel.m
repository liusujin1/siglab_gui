function [] = toexcel(DataType,Action)      
% Kerry Schutz, DSP Technology
% function [] = toexcel(DataType,Action)
% toexcel.m links SigLab VIs (vos,vsa,vna,vss,vid) with MS Excel to create
% text files and plot the acquired data in Excel.
% 
% The "Plot in Excel" menu selection opens up Excel and plots results.
% The "Save to Text File" and "Save As to Text File" selections saves
% results to a text file in a form that can be read later in Excel.

global HVSS_;
global VSSpath;  % with trailing \
global VSSfile;

global HVID_;
global VID_PATH;
global VID_FILE;
global VID_DATA;
tab = setstr(9);

switch DataType
case 'Time Series'
   toexcel('vos',Action);
   return;
case 'Auto-Spectrum'
   toexcel('vsa',Action);
   return;
case 'Transfer Function'
   toexcel('vna',Action);
   return;
case 'Cross-Spectrum'
case 'Coherence'
case 'Auto-Correlation'
case 'Cross-Correlation'
case 'Impulse Response'
case 'Complex FFT'
case 'Capture Buffer'

case 'vos'
   [a b c d vxx]=v_dlg1('get','state');
   eval(['a = ',vxx,'(''get'',''meas'');'])
   Tvec = a.tdxvec;
   for i = a.clist
       TimeDat(:,i) = a.scmeas(i).tdmeas*a.scmeas(i).euscale_fac;
   end;
   TimeMap=a.clist;
   eval(['[SystemClk SampleRate CenterFreq] = ',vxx,'(''get'',''acqpar'');']);
   eval(['[vpath,vfile]                     = ',vxx,'(''get'',''file'');']);
  
   Npts = length(Tvec);
   Nmeasurements = length(a.clist);
   time = clock;
   ampm = 'AM';
   if time(4)>=13
       time(4)=time(4)-12;
       ampm = 'PM';
   end;
   hour = num2str(time(4));
   minutes = num2str(time(5));
   seconds = num2str(time(6));
   if length(minutes)==1
       minutes = ['0',minutes];
   end;
   timestamp = [hour,':',minutes,':',seconds,ampm];
   Row1 = ['DSPT SigLab VOS Data File created ',date,' at ',timestamp];
   Row2 = [tab 'General Setup Parameters'];
   ChannelString = ['( ',num2str(TimeMap(1))];  
   if length(TimeMap) > 1
       for i = 2:length(TimeMap)
           ChannelString = [ChannelString,',',num2str(TimeMap(i))]; 
       end
   end;
   ChannelString = [ChannelString ' )'];   
   Row3 = ['Acquired data on ',num2str(Nmeasurements) ' channels ' ChannelString];
   Row4 = ['Acquired ',num2str(Npts) ' points per channel'];
   Row5 = ['Sampled at ',ftoa('%7.0f',SampleRate),' Hz'];
   Row6 = ['All channels have Engineering Units applied if relevant'];
   if isempty(a.ovld)
       Row7 = ['Overload status not implemented'];    
   else
       if a.ovld == 0
           Row7 = ['No overloads on any channel'];
       else
           Row7 = ['Had Overloads on ',length(a.ovld),' channels: (',num2str(a.ovld),')'];
       end;
   end
   Row8 = ['Performed ',num2str(a.navg),' averages'];
   Row9 = ['Zoom Center Frequency = ',num2str(a.zoomcf),' Hz'];
   Row10 = [];
   for i = a.clist
       if a.scmeas(i).eu_on_off
           Row10 = [Row10,[a.scmeas(i).label,' (',a.scmeas(i).eu_string,')'],tab];    
       else
           Row10 = [Row10,[a.scmeas(i).label,' (Volts)'],tab];
       end;  
   end
   Row10 = ['Time(sec)' tab Row10];
   DataLump = [Tvec' TimeDat];   
case 'vsa'
   [a b c d vxx]=v_dlg1('get','state');
   eval(['a = ',vxx,'(''get'',''meas'');'])
   eval(['[SystemClk SampleRate CenterFreq] = ',vxx,'(''get'',''acqpar'');']);
   eval(['[vpath,vfile]                     = ',vxx,'(''get'',''file'');']);
   SpectrumsExist = 0;
   for i = a.clist
       if ~isempty(a.scmeas(a.clist(i)).aspec)
           SpectrumsExist = 1;    
       end;
   end;
   if ~SpectrumsExist 
       msgbox('vsa only writes spectrum text data to disk and/or Excel');
       return;
   end;
   Fvec = a.fdxvec;
   for i = a.clist
       AspecDat(:,i) = a.scmeas(i).aspec*(a.scmeas(i).euscale_fac/a.scmeas(i).db_ref)^2*a.wincor;
   end;
   AspecMap = a.clist;
   
   Npts = length(a.fdxvec);
   Nmeasurements = length(a.clist);
   time = clock;
   ampm = 'AM';
   if time(4)>=13
       time(4)=time(4)-12;
       ampm = 'PM';
   end;
   hour = num2str(time(4));
   minutes = num2str(time(5));
   seconds = num2str(time(6));
   if length(minutes)==1
       minutes = ['0',minutes];
   end;
   timestamp = [hour,':',minutes,':',seconds,ampm];
   Row1 = ['DSPT SigLab VSA Data File created ',date,' at ',timestamp];
   Row2 = [tab 'General Setup Parameters'];
   ChannelString = ['( ',num2str(AspecMap(1))];  
   if length(AspecMap) > 1
       for i = 2:length(AspecMap)
           ChannelString = [ChannelString,',',num2str(AspecMap(i))]; 
       end
   end;
   ChannelString = [ChannelString ' )'];   
   Row3 = ['Acquired data on ',num2str(Nmeasurements) ' channels ' ChannelString];
   Row4 = ['Acquired ',num2str(Npts) ' frequency points per channel'];
   Row5 = ['Sampled at ',ftoa('%7.0f',SampleRate),' Hz'];
   Row6 = ['All channels listed in dB = 10*log10(Autospectrum)'];
      
   if isempty(a.ovld)
       Row7 = ['Overload status not implemented'];    
   else
       if a.ovld == 0
           Row7 = ['No overloads on any channel'];
       else
           Row7 = ['Had Overloads on ',length(a.ovld),' channels: (',num2str(a.ovld),')'];
       end;
   end
   Row8 = ['Performed ',num2str(a.navg),' averages'];
   Row9 = ['Zoom Center Frequency = ',num2str(a.zoomcf),' Hz'];
   Row10 = [];
   for i = a.clist
       if a.scmeas(i).eu_on_off
           Row10 = [Row10,[a.scmeas(i).label,' (',a.scmeas(i).eu_string,'^2 RMS)'],tab];    
       else
           Row10 = [Row10,[a.scmeas(i).label,' (V^2 RMS)'],tab];
       end;  
   end
   Row10 = ['Frequency (Hz)' tab Row10];
	DataLump = [Fvec' 10*log10(AspecDat)];   
case 'vna'
   [a b c d vxx]=v_dlg1('get','state');
   eval(['a = ',vxx,'(''get'',''meas'');'])
   eval(['[SystemClk SampleRate CenterFreq] = ',vxx,'(''get'',''acqpar'');']);
   eval(['[vpath,vfile]                     = ',vxx,'(''get'',''file'');']);
   if ~isfield(a,'xcstate')
       errordlg(['no transfer function data available in SLm structure'],'Oops!','error')
       return;
   end;
   Fvec = a.fdxvec;
   XferRef = a.xcstate.refc;
   XferResp = a.xcstate.resp;
   NumRefs = length(XferRef);
   Refs = [];
   for i=1:NumRefs
       Refs = [Refs; XferRef(i)*ones(1,length(XferResp(1).r))'];    
   end;  
   Resps=[];
   for i=1:NumRefs
       Resps =[Resps; XferResp(i).r']; 
   end;
   XferMap = [Refs Resps];
   [NumXfers b]=size(XferMap);
   for i=1:NumXfers
       yscale(i) = a.scmeas(XferMap(i,2)).euscale_fac/a.scmeas(XferMap(i,1)).euscale_fac;
		 % db_ref(i) = a.scmeas(XferMap(i,2)).db_ref/a.scmeas(XferMap(i,1)).db_ref;
		 db_ref(i) = 1;
       XferDat(:,i) = (yscale(i)/db_ref(i))*a.xcmeas(XferMap(i,1),XferMap(i,2)).xfer;
   end;

   % next 2 lines taken from plot_vna.mi
   VSFNc  = 1e-307; % very small number
   DB20c  = 8.68588963806504;  % 20*log10(x) = DB20c*log(x)
 
   Npts = length(a.fdxvec);
   Nmeasurements = NumXfers;
   time = clock;
   ampm = 'AM';
   if time(4)>=13
       time(4)=time(4)-12;
       ampm = 'PM';
   end;
   hour = num2str(time(4));
   minutes = num2str(time(5));
   seconds = num2str(time(6));
   if length(minutes)==1
       minutes = ['0',minutes];
   end;
   timestamp = [hour,':',minutes,':',seconds,ampm];
   Row1 = ['DSPT SigLab VNA Data File created ',date,' at ',timestamp];
   Row2 = ['Setup Parameters: transfer functions using ',num2str(NumRefs),' reference chans'];
   ChannelString = ['( ',num2str(XferMap(1,2)),'/',num2str(XferMap(1,1))];  
   [rows cols]=size(XferMap);
   for i = 2:rows
       ChannelString = [ChannelString,',',num2str(XferMap(i,2)),'/',num2str(XferMap(i,1))]; 
   end
   ChannelString = [ChannelString ' )'];   
   Row3 = ['Measured ',num2str(Nmeasurements) ' transfer functions: ' ChannelString];
   Row4 = ['Acquired ',num2str(Npts) ' frequency points per channel'];
   Row5 = ['Sampled at ',ftoa('%7.0f',SampleRate),' Hz'];
   Row6 = ['Magnitude displayed in dB = 20*log10(abs(XferDat)),  Phase in degrees(unwrapped):(180/pi) * unwrap(angle(XferDat)) '];    
   if isempty(a.ovld)
       Row7 = ['Overload status not implemented'];    
   else
       if a.ovld == 0
           Row7 = ['No overloads on any channel'];
       else
           Row7 = ['Had Overloads on ',length(a.ovld),' channels: (',num2str(a.ovld),')'];
       end;
   end
   Row8 = ['Performed ',num2str(a.navg),' averages'];
   Row9 = ['Zoom Center Frequency = ',num2str(a.zoomcf),' Hz'];

   for i = 1:Nmeasurements
       if a.scmeas(XferMap(i,1)).eu_on_off == 0
           a.scmeas(XferMap(i,1)).eu_string = 'V';
       end;
       if a.scmeas(XferMap(i,2)).eu_on_off == 0
           a.scmeas(XferMap(i,2)).eu_string = 'V';
       end;
   end;
   Row10 = ['Magnitude ' num2str(XferMap(1,2)) '/' num2str(XferMap(1,1)),...
               '(',a.scmeas(XferMap(1,2)).eu_string,'/',a.scmeas(XferMap(1,1)).eu_string,')',...
           tab 'Phase ' num2str(XferMap(1,2)) '/' num2str(XferMap(1,1)),...
           '(degrees)'];   
   for i = 2:Nmeasurements
      Row10 = [Row10,tab,'Magnitude ',num2str(XferMap(i,2)) '/' num2str(XferMap(i,1)),...
               '(',a.scmeas(XferMap(i,2)).eu_string,'/',a.scmeas(XferMap(i,1)).eu_string,')',...
              tab,'Phase ',num2str(XferMap(i,2)) '/' num2str(XferMap(i,1)),...
              '(degrees)'];
   end
   Row10 = ['Frequency (Hz)' tab Row10];
   DataLump = [];
   Magnitude = 20*log10(abs(XferDat+VSFNc));
   Phase = (180/pi) * unwrap(angle(XferDat)); 
   for i = 1:Nmeasurements
      DataLump = [DataLump Magnitude(:,i) Phase(:,i)];
  end  
  DataLump = [Fvec' DataLump];
case 'vss'
   [Fvec XferDat XferMap] = vss('get','meas');
   Npts = length(Fvec);
   Nmeasurements = length(XferMap(:,1));
   Row1 = ['DSPT SigLab VSS Data File'];
   Row2 = [tab 'General Setup Parameters'];
   ChannelString = '( 1';   
   for i = 1:Nmeasurements
      ChannelString = [ChannelString,',',num2str(XferMap(i,2))]; 
   end
   ChannelString = [ChannelString ' )'];   
   Row3 = ['Acquired data on ',num2str(min(size(XferDat))+1) ' channels ' ChannelString];
   Row4 = ['Acquired ',num2str(Npts) ' points per channel'];
   Row5 = ['Sampled at ','(not relevant to swept-sine)'];
   Row6 = ['Magnitude displayed in 20*log10(abs(XferDat)),  Phase in degrees(unwrapped):(180/pi) * unwrap(angle(XferDat)) '];
   Row7 = ['User defineable slot in toexcel.m'];
   Row8 = ['User defineable slot in toexcel.m'];
   Row9 = ['User defineable slot in toexcel.m'];
   Row10 = ['Magnitude ' num2str(XferMap(1,2)) '/' num2str(XferMap(1,1)),...
           tab 'Phase ' num2str(XferMap(1,2)) '/' num2str(XferMap(1,1)) ];   
   for i = 2:Nmeasurements
      Row10 = [Row10,tab,'Magnitude ',num2str(XferMap(i,2)) '/' num2str(XferMap(i,1)),...
              tab,'Phase ',num2str(XferMap(i,2)) '/' num2str(XferMap(i,1))];
   end
   Row10 = ['Frequency (Hz)' tab Row10];
   DataLump = [];
   Magnitude = 20*log10(abs(XferDat));
   Phase = (180/pi) * unwrap(angle(XferDat)); 
   for i = 1:Nmeasurements
      DataLump = [DataLump Magnitude(:,i) Phase(:,i)];
   end   
   DataLump = [Fvec DataLump];
case 'vid'
   [plane zeros poles misc EU]             = vid('get','model');
   [vpath vfile Tvec TimeDat TimeMap Navg] = vid('get','file');
   excitation = TimeDat(:,1);
   response   = TimeDat(:,2);    
   Fs = 1000/abs(Tvec(1024)-Tvec(1023));  % Tvec is in milliseconds!!
   SampleRate = Fs; 
   gain = misc(1);   % 'misc' stands for 'misc'ellaneous not 'misc'onceived
   order = length(poles);
   N = length(excitation);
   OversamplingRate = 2.56;  
   Fvec = [0:Fs/N:Fs-Fs/N]';
   Fvec = Fvec(1:N/OversamplingRate);
   % FFT-BASED ESTIMATES: MAGNITUDE AND PHASE
   fft_estimate = fft(response)./fft(excitation);       
   fft_magnitude = 20*log10(abs(fft_estimate(1:N/OversamplingRate)));
   fft_phase = (180/pi)*unwrap(angle(fft_estimate(1:N/OversamplingRate))); 
   % POLE/ZERO-BASED ESTIMATES: MAGNITUDE AND PHASE
   zeros_expansion = poly(zeros);
   poles_expansion = poly(poles);
   fft_num = fft(zeros_expansion,1024);  % length of excitation sequence = 1024
   fft_den = fft(poles_expansion,1024); 
   pz_estimate = gain * fft_num./fft_den;
   pz_magnitude = 20*log10(abs(pz_estimate(1:N/OversamplingRate)))';
   pz_phase = (180/pi)*unwrap(angle(pz_estimate(1:N/OversamplingRate)))';
   Npts = length(Fvec);
   [Nmeasurements dummy] = size(TimeMap);
   Row1 = ['DSPT SigLab VID Data File'];
   Row2 = [tab 'General Setup Parameters'];
   ChannelString = '( 1 ';   
   for i = 1:Nmeasurements
      ChannelString = [ChannelString,',',num2str(TimeMap(i,2))]; 
   end
   ChannelString = [ChannelString ' )'];   
   Row3 = ['Acquired data on ',num2str(length(TimeMap)) ' channels ' ChannelString];
   Row4 = ['Acquired ',num2str(Npts) ' points per channel'];
   Row5 = ['Sampled at ',ftoa('%7.0f',SampleRate),' Hz, Model Order = ' num2str(order)];
   Row6 = ['Magnitude displayed in dB, Phase in degrees (unwrapped)'];
   Row7 = ['User defineable slot in toexcel.m'];
   Row8 = ['User defineable slot in toexcel.m'];
   Row9 = ['User defineable slot in toexcel.m'];
   Row10 = ['pz_magnitude ' num2str(TimeMap(1,2)) '/' num2str(TimeMap(1,1)),...
           tab 'pz_phase ' num2str(TimeMap(1,2)) '/' num2str(TimeMap(1,1)),...
           tab 'fft_magnitude ' num2str(TimeMap(1,2)) '/' num2str(TimeMap(1,1)),...
           tab 'fft_phase ' num2str(TimeMap(1,2)) '/' num2str(TimeMap(1,1))];   
   for i = 2:Nmeasurements
      Row10 = [Row10,tab,'pz_magnitude ',num2str(TimeMap(i,2)) '/' num2str(TimeMap(i,1)),...
              tab,'pz_phase ',num2str(TimeMap(i,2)) '/' num2str(TimeMap(i,1)),...
              tab,'fft_magnitude ',num2str(TimeMap(i,2)) '/' num2str(TimeMap(i,1)),...
              tab,'fft_phase ',num2str(TimeMap(i,2)) '/' num2str(TimeMap(i,1))];
   end
   Row10 = ['Frequency (Hz)' tab Row10];
   DataLump = [];
   pz_estimates = [pz_magnitude pz_phase];
   fft_estimates = [fft_magnitude fft_phase];
   for i = 1:Nmeasurements
      DataLump = [DataLump pz_estimates(:,i:i+1) fft_estimates(:,i:i+1)];
   end   
   DataLump = [Fvec DataLump];
case 'vto'
   eval('[BandFreq OctaveDat OctaveMap UnitString] = vto(''get'',''meas'');')
   if isempty(OctaveDat)
       msgbox('vto only writes octave text data to disk and/or to Excel');
       return;
   end;
   eval('[SystemClk SampleRate Frac Order] = vto(''get'',''acqpar'');')
   eval('[vpath,vfile]                     = vto(''get'',''file'');'); 
   Npts = length(BandFreq);
   OctaveDat = OctaveDat(1:Npts,:);
   Nmeasurements = min(size(OctaveDat));
   time = clock;
   ampm = 'AM';
   if time(4)>=13 time(4)=time(4)-12; ampm = 'PM'; end;
   hour    = num2str(time(4));
   minutes = num2str(time(5));
   seconds = num2str(time(6));
   if length(minutes)==1  minutes = ['0',minutes];  end;
   timestamp = [hour,':',minutes,':',seconds,ampm];
   Row1 = ['DSPT SigLab VTO Octave Data created ',date,' at ',timestamp];
   Row2 = [tab 'General Setup Parameters'];
   ChannelString = ['( ',num2str(OctaveMap(1))];  
   if length(OctaveMap) > 1
       for i = 2:length(OctaveMap)
           ChannelString = [ChannelString,',',num2str(OctaveMap(i))]; 
       end
   end;
   ChannelString = [ChannelString ' )'];   
   Row3 = ['Acquired data on ',num2str(Nmeasurements) ' channels ' ChannelString];
   Row4 = ['Analyzed on ',num2str(Npts) ' bands per channel, Order = ',num2str(Order)];
   Row5 = ['Sampled at ',ftoa('%7.0f',SampleRate),' Hz'];
   Row6 = ['All channels displayed in ',UnitString];
   Row7 = ['User defineable slot in toexcel.m'];
   Row8 = ['User defineable slot in toexcel.m'];
   Row9 = ['User defineable slot in toexcel.m'];
   Row10 = 'Channel 1';  
   for i = 2:Nmeasurements
      Row10 = [Row10,tab,'Channel ',num2str(OctaveMap(i))];
   end
   Row10 = ['1/',num2str(Frac),' Band Center Freqs' tab 'Band Number' tab Row10];
   BandNumbers = round(log10(BandFreq) * 10 * Frac/3);
   DataLump = [BandFreq BandNumbers OctaveDat];   
case 'vto narrowband'
   eval('[Fvec AspecDat AspecMap] = vto(''get'',''psd'');');
   eval('[SystemClk SampleRate Frac Order] = vto(''get'',''acqpar'');');
   eval('[vpath,vfile]                     = vto(''get'',''file'');'); 
   Npts = length(Fvec);
   Nmeasurements = length(AspecMap);
   if ~Nmeasurements msgbox('smap only writes spectrum data to Excel');
                     return;
   end;
   time = clock;
   ampm = 'AM';
   if time(4)>=13 time(4)=time(4)-12; ampm = 'PM'; end;
   hour    = num2str(time(4));
   minutes = num2str(time(5));
   seconds = num2str(time(6));
   if length(minutes)==1  minutes = ['0',minutes];  end;
   timestamp = [hour,':',minutes,':',seconds,ampm];
   Row1 = ['DSPT SigLab VSA Autospectra Data from VTO created ',date,' at ',timestamp];
   Row2 = [tab 'General Setup Parameters'];
   ChannelString = ['( ',num2str(AspecMap(1))];  
   if length(AspecMap) > 1
       for i = 2:length(AspecMap)
           ChannelString = [ChannelString,',',num2str(AspecMap(i))]; 
       end
   end;
   ChannelString = [ChannelString ' )'];   
   Row3 = ['Acquired data on ',num2str(Nmeasurements) ' channels ' ChannelString];
   Row4 = ['Acquired ',num2str(Npts) ' frequency points per channel'];
   Row5 = ['Sampled at ',ftoa('%7.0f',SampleRate),' Hz'];
   Row6 = 'All channels in same units as narrowband measurement displayed in vto ';  % RAB 01/15/01
   Row7 = ' ';
   Row8 = ' ';
   Row9 = ' ';
   Row10 = ['Channel ',num2str(AspecMap(1))];
   for i = 2:Nmeasurements
      Row10 = [Row10,tab,'Channel ',num2str(AspecMap(i))];
   end
   Row10 = ['Frequency (Hz)' tab Row10];

   % DataLump = [Fvec 10*log10(AspecDat)]; changed 01/15/01 RAB  
   DataLump = [Fvec AspecDat];   % AspecDat now returns actual displayed data, so EUs etc. come through
                                 % RAB 01/15/01 

case 'smap'
   [Fvec AspecDat AspecMap] = smap('get','psd');
   [SystemClk SampleRate]   = smap('get','acqpar');
   [vpath,vfile]            = smap('get','file');
   Npts = length(Fvec);
   Nmeasurements = length(AspecMap);
   if ~Nmeasurements msgbox('smap only writes spectrum data to Excel');
                     return;
   end;
   time = clock;
   ampm = 'AM';
   if time(4)>=13 time(4)=time(4)-12; ampm = 'PM'; end;
   hour    = num2str(time(4));
   minutes = num2str(time(5));
   seconds = num2str(time(6));
   if length(minutes)==1  minutes = ['0',minutes];  end;
   timestamp = [hour,':',minutes,':',seconds,ampm];
   Row1 = ['DSPT SigLab VSA or SMAP autospectra file created ',date,' at ',timestamp];
   Row2 = [tab 'General Setup Parameters'];
   ChannelString = ['( ',num2str(AspecMap(1))];  
   if length(AspecMap) > 1
       for i = 2:length(AspecMap)
           ChannelString = [ChannelString,',',num2str(AspecMap(i))]; 
       end
   end;
   ChannelString = [ChannelString ' )'];   
   Row3 = ['Acquired data on ',num2str(Nmeasurements) ' channels ' ChannelString];
   Row4 = ['Acquired ',num2str(Npts) ' frequency points per channel'];
   Row5 = ['Sampled at ',ftoa('%7.0f',SampleRate),' Hz'];
   Row6 = 'All channels listed in dB = 10*log10(Autospectrum)';
   Row7 = ' ';
   Row8 = ' ';
   Row9 = ' ';
   Row10 = ['Channel ',num2str(AspecMap(1))];
   for i = 2:Nmeasurements
      Row10 = [Row10,tab,'Channel ',num2str(AspecMap(i))];
   end
   Row10 = ['Frequency (Hz)' tab Row10];
   DataLump = [Fvec 10*log10(AspecDat)];   

otherwise
   disp(['DataType not recognized: ',DataType]);
end; % end swith for data type

if strcmp(Action,'Save to Text')
 	txtFile = vfile;
    txtPath = vpath;
elseif strcmp(Action,'Save As to Text')
 	default_ext  = '*.txt';                     
 	[file_n,path_n]=uiputfile([vpath,default_ext],'Open File',0.5,0.5);
    if file_n == 0 
        return;
  	 else
   	txtFile = file_n;  	
    	txtPath = path_n;
    end;
elseif strcmp(Action,'Plot in Excel')
  	txtFile = vfile;
	txtPath = vpath;
end
DotPosition = find(txtFile == '.');	
if isempty(DotPosition)
 	txtFile = [txtFile,'.txt'];
else
 	txtFile = [txtFile(1:DotPosition-1),'.','txt']; 
end;
Row1 = [Row1,' ','for ',vpath,txtFile];
txtFID = fopen([txtPath txtFile],'wt+');

if txtFID ~= -1  
   count = fprintf(txtFID,...
   '%s\n\n%s\n%s\n%s\n%s\n%s\n%s\n%s\n%s\n\n%s\n',...
   Row1,Row2,Row3,Row4,Row5,Row6,Row7,Row8,Row9,Row10);
   for i = 1:Npts
      for j = 1:min(size(DataLump))
         count = fprintf(txtFID,'%6.8f\t',DataLump(i,j));
      end
      fprintf(txtFID,'\n');
   end
   fclose(txtFID);
   if sum(count) == 0
      disp([txtFile ' was not written successfully: 0 bytes'])
      disp('Check to ensure that file is not currently open')
   end
else       
   disp([txtFile ' was not opened successfully'])
end

if strcmp(Action,'Plot in Excel')
   % The variable "Path2Exe" variable should reflect where excel.exe 
   % is located on your machine. Path2Exe's string value was set 
   % when your SigLab software was installed. It uses the 
   % Microsoft short path notation because the bang(!) operator in MATLAB 
   % cannot process spaces in the path. The short path notation is
   % indicated by the tilda (~) characters. If your path
   % to excel.exe changes, you must edit the Path2Exe variable using
   % the short path notation. This can be found by typing "dir" in 
   % a DOS box from where excel.exe is located.

%  Path2Exe = 'c:\PROGRA~1\MICROS~1\OFFICE\excel.exe';
   Path2Exe = 'd:\MICROS~1\OFFICE\excel.exe';

           
   if exist(Path2Exe)
   	eval(['!',Path2Exe,' ',txtPath,txtFile,' &'],' ');
   else
       errordlg([Path2Exe,...
               ' does not exist. You must edit the Path2Exe variable in toexcel.m'],'Oops');
   end
end; % end check for Action 'Plot in Excel'
